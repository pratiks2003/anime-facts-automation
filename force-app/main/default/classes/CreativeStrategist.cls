/**
 * Creative Strategist agent: before a Reel is written it decides WHAT to make and HOW it should look:
 * format, angle, target length and a Style (pick an existing one, give a Testing style its trial, or invent one).
 *
 * Code owns the explore/exploit balance (so learning is guaranteed); the agent owns the creative call.
 */
public with sharing class CreativeStrategist {
    public static final String TEMPLATE = 'Insta_Creative_Strategist';
    public static final Decimal TEST_RATE = 0.35;
    public static final Decimal INVENT_RATE = 0.15;
    public static final Integer MAX_TESTING = 3;
    @TestVisible
    private static Decimal roll;

    /**
     * request: {content_id, slot, scheduled_format, formats:[{name, about}], anime_hint, topic_hint}
     * returns: {format, angle, topic, anime, length_seconds, mode, style_name, style:{spec}, reasoning}
     */
    public static Map<String, Object> decide(Map<String, Object> request) {
        StyleLibrary.ensureSeeds();
        try {
            StyleLibrary.refreshStats();
        } catch (Exception e) {
            System.debug(LoggingLevel.WARN, 'style stats refresh failed: ' + e.getMessage());
        }
        List<Style__c> styles = StyleLibrary.usable();
        String mode = chooseMode(styles);

        List<Object> library = new List<Object>();
        for (Style__c s : styles) {
            library.add(StyleLibrary.describe(s));
        }
        Map<String, Object> agentReq = new Map<String, Object>{
            'mode' => mode,
            'slot' => request.get('slot'),
            'scheduled_format' => request.get('scheduled_format'),
            'formats' => request.get('formats'),
            'anime_hint' => request.get('anime_hint'),
            'topic_hint' => request.get('topic_hint'),
            'styles' => library,
            'recent_reels' => recentReels(),
            'trend_brief' => latestBrief(),
            'lessons' => lessons(),
            'style_limits' => request.get('style_limits')
        };
        Map<String, Object> out = PromptRunner.runJson(TEMPLATE, agentReq);

        Style__c chosen = pickStyle(out, styles, mode);
        Boolean hot = request.get('slot') == 'Morning' || out.get('breaking') == true;
        Map<String, Object> decision = new Map<String, Object>{
            'format' => hot ? allowedFormat(out.get('format'), request) : validFormat(out.get('format'), request),
            'hot_topic' => out.get('hot_topic'),
            'breaking' => out.get('breaking') == true,
            'angle' => out.get('angle'),
            'topic' => out.get('topic'),
            'anime' => out.get('anime'),
            'length_seconds' => out.get('length_seconds'),
            'mode' => mode,
            'style_name' => chosen.Name,
            'style' => JSON.deserializeUntyped(chosen.Spec__c),
            'reasoning' => out.get('reasoning'),
            'needs_trending_sound' => out.get('needs_trending_sound') == true
        };
        record((String) request.get('content_id'), chosen, decision);
        return decision;
    }

    /** exploit (best proven style) | test (give a Testing style its trial) | invent (new style). */
    @TestVisible
    private static String chooseMode(List<Style__c> styles) {
        Decimal r = roll != null ? roll : Math.random();
        Integer testing = 0;
        Boolean needsTrial = false;
        for (Style__c s : styles) {
            if (s.Status__c == 'Testing') {
                testing++;
                needsTrial = needsTrial || s.Reels__c == null || s.Reels__c < StyleLibrary.TRIAL_REELS;
            }
        }
        if (needsTrial && r < TEST_RATE) {
            return 'test';
        }
        if (testing < MAX_TESTING && r >= TEST_RATE && r < TEST_RATE + INVENT_RATE) {
            return 'invent';
        }
        return 'exploit';
    }

    private static Style__c pickStyle(Map<String, Object> out, List<Style__c> styles, String mode) {
        if (mode == 'invent' && out.get('new_style') instanceof Map<String, Object>) {
            Map<String, Object> ns = (Map<String, Object>) out.get('new_style');
            Object spec = ns.get('spec');
            String name = String.valueOf(ns.get('name') == null ? 'Invented ' + Datetime.now().format('MMdd-HHmm') : ns.get('name')).abbreviate(80);
            if (spec instanceof Map<String, Object>) {
                ((Map<String, Object>) spec).put('name', name);
                for (Style__c s : styles) {
                    if (s.Name == name) {
                        name = (name + ' ' + Datetime.now().format('MMdd')).abbreviate(80);
                    }
                }
                Style__c created = new Style__c(
                    Name = name,
                    Status__c = 'Testing',
                    Origin__c = 'Invented',
                    Description__c = String.valueOf(ns.get('why') == null ? '' : ns.get('why')).abbreviate(2000),
                    Spec__c = JSON.serialize(spec)
                );
                insert created;
                return created;
            }
        }
        String wanted = String.valueOf(out.get('style_name'));
        for (Style__c s : styles) {
            if (s.Name == wanted && (mode != 'test' || s.Status__c == 'Testing')) {
                return s;
            }
        }
        // the agent named nothing usable: code picks per mode
        for (Style__c s : styles) {
            if (mode == 'test' && s.Status__c == 'Testing') {
                return s;
            }
        }
        for (Style__c s : styles) {
            if (s.Status__c == 'Active') {
                return s; // ordered by performance
            }
        }
        return styles[0];
    }

    /** Hot Reels (morning / breaking news): any allowed format, the topic decides; no variety rule. */
    @TestVisible
    private static String allowedFormat(Object proposed, Map<String, Object> request) {
        if (proposed != null && request.get('formats') instanceof List<Object>) {
            for (Object f : (List<Object>) request.get('formats')) {
                Object name = f instanceof Map<String, Object> ? ((Map<String, Object>) f).get('name') : f;
                if (String.valueOf(name) == String.valueOf(proposed)) {
                    return String.valueOf(proposed);
                }
            }
        }
        return (String) request.get('scheduled_format');
    }

    /** The agent's format if it is allowed and not one of the last 2 Reels' formats (variety); else the rotation's. */
    @TestVisible
    private static String validFormat(Object proposed, Map<String, Object> request) {
        String scheduled = (String) request.get('scheduled_format');
        if (proposed == null || !(request.get('formats') instanceof List<Object>)) {
            return scheduled;
        }
        Boolean allowed = false;
        for (Object f : (List<Object>) request.get('formats')) {
            Object name = f instanceof Map<String, Object> ? ((Map<String, Object>) f).get('name') : f;
            allowed = allowed || String.valueOf(name) == String.valueOf(proposed);
        }
        if (!allowed) {
            return scheduled;
        }
        String contentId = (String) request.get('content_id');
        for (Anime_Content__c c : [
            SELECT Format__c FROM Anime_Content__c
            WHERE Status__c IN ('Published', 'Rendered', 'Approved', 'Publishing') AND External_Id__c != :contentId
            ORDER BY CreatedDate DESC LIMIT 2
        ]) {
            if (c.Format__c == String.valueOf(proposed)) {
                return scheduled; // same format as one of the last 2 Reels: keep the feed varied
            }
        }
        return String.valueOf(proposed);
    }

    private static List<Object> recentReels() {
        List<Object> rows = new List<Object>();
        for (Anime_Content__c c : [
            SELECT Format__c, Anime__r.Name, Hook__c, Quality_Score__c, Style__r.Name, Topic__c, Slot__c, CreatedDate, Strategy__c,
                (SELECT Views__c, Avg_Watch_Time_Ms__c, Shares__c, Saves__c FROM Social_Posts__r LIMIT 1)
            FROM Anime_Content__c
            WHERE Status__c IN ('Published', 'Rendered', 'Approved')
            ORDER BY CreatedDate DESC
            LIMIT 12
        ]) {
            Map<String, Object> row = new Map<String, Object>{
                'format' => c.Format__c, 'anime' => c.Anime__r == null ? null : c.Anime__r.Name, 'hook' => c.Hook__c,
                'topic' => c.Topic__c, 'style' => c.Style__r == null ? null : c.Style__r.Name, 'score' => c.Quality_Score__c,
                'slot' => c.Slot__c, 'created' => c.CreatedDate,
                'hot' => c.Strategy__c != null && c.Strategy__c.contains('"hot_topic":{')
            };
            if (!c.Social_Posts__r.isEmpty()) {
                Social_Post__c p = c.Social_Posts__r[0];
                row.put('views', p.Views__c);
                row.put('avg_watch_ms', p.Avg_Watch_Time_Ms__c);
                row.put('shares', p.Shares__c);
                row.put('saves', p.Saves__c);
            }
            rows.add(row);
        }
        return rows;
    }

    private static Object latestBrief() {
        List<Trend_Brief__c> briefs = [SELECT Brief_Date__c, Summary__c, Brief_JSON__c FROM Trend_Brief__c ORDER BY Brief_Date__c DESC, CreatedDate DESC LIMIT 1];
        if (briefs.isEmpty()) {
            return null;
        }
        try {
            return JSON.deserializeUntyped(briefs[0].Brief_JSON__c);
        } catch (Exception e) {
            return briefs[0].Summary__c;
        }
    }

    private static List<String> lessons() {
        List<String> out = new List<String>();
        for (Performance_Review__c r : [SELECT Hook_Lessons__c FROM Performance_Review__c ORDER BY Period_End__c DESC LIMIT 1]) {
            if (r.Hook_Lessons__c != null) {
                out.addAll(r.Hook_Lessons__c.split('\n'));
            }
        }
        for (Reference_Reel__c r : [SELECT Lessons__c FROM Reference_Reel__c WHERE Status__c = 'Done' ORDER BY Analyzed_At__c DESC LIMIT 3]) {
            if (r.Lessons__c == null) {
                continue;
            }
            out.addAll(r.Lessons__c.split('\n'));
        }
        return out;
    }

    private static void record(String contentId, Style__c style, Map<String, Object> decision) {
        style.Last_Used__c = Datetime.now();
        update new Style__c(Id = style.Id, Last_Used__c = style.Last_Used__c);
        if (String.isBlank(contentId)) {
            return;
        }
        List<Anime_Content__c> rows = [SELECT Id FROM Anime_Content__c WHERE External_Id__c = :contentId LIMIT 1];
        if (rows.isEmpty()) {
            return;
        }
        Map<String, Object> summary = decision.clone();
        summary.remove('style');
        update new Anime_Content__c(
            Id = rows[0].Id,
            Style__c = style.Id,
            Strategy__c = JSON.serialize(summary).abbreviate(5000),
            Needs_Trending_Sound__c = decision.get('needs_trending_sound') == true
        );
    }
}
