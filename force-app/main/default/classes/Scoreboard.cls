/**
 * Scoreboard: turns Instagram results into one number per Reel and evidence about what works.
 *
 * Performance index = 0.6 x views / median views + 0.4 x watch time / median watch time
 * (medians over this channel's published Reels of the last 30 days that are at least 12 hours old).
 * 1.00 = a normal Reel for this account. Verdict: Winner at 1.25+, Loser at 0.75 or less, Too young under 12 h.
 *
 * insights() groups scored Reels by show, format, slot, effect, style and planner, and applies the playbook's
 * drop rule (e.g. 2 losers in a row on a show -> drop it for 48 h).
 */
public with sharing class Scoreboard {
    public static final Integer MIN_AGE_HOURS = 12;
    public static final Decimal WINNER = 1.25;
    public static final Decimal LOSER = 0.75;

    /** Re-scores the last 30 days of published Reels. Returns how many Reels changed. */
    public static Integer refresh(Datetime now) {
        List<Social_Post__c> posts = [
            SELECT Content__c, Published_At__c, Views__c, Avg_Watch_Time_Ms__c, Content__r.Performance_Index__c, Content__r.Verdict__c
            FROM Social_Post__c
            WHERE Status__c = 'Published' AND Content__c != null AND Is_Trial__c = false AND Published_At__c >= :now.addDays(-30)
        ];
        List<Decimal> views = new List<Decimal>();
        List<Decimal> watch = new List<Decimal>();
        for (Social_Post__c p : posts) {
            if (ageHours(p.Published_At__c, now) >= MIN_AGE_HOURS && p.Views__c != null) {
                views.add(p.Views__c);
                if (p.Avg_Watch_Time_Ms__c != null) watch.add(p.Avg_Watch_Time_Ms__c);
            }
        }
        Decimal medViews = median(views);
        Decimal medWatch = median(watch);
        Map<Id, Anime_Content__c> updates = new Map<Id, Anime_Content__c>();
        for (Social_Post__c p : posts) {
            Decimal index = null;
            String verdict;
            if (ageHours(p.Published_At__c, now) < MIN_AGE_HOURS) {
                verdict = 'Too young';
            } else if (p.Views__c == null || medViews == null || medViews == 0) {
                verdict = 'No data';
            } else {
                index = index(p.Views__c, p.Avg_Watch_Time_Ms__c, medViews, medWatch);
                verdict = index >= WINNER ? 'Winner' : (index <= LOSER ? 'Loser' : 'Average');
            }
            if (p.Content__r.Verdict__c != verdict || p.Content__r.Performance_Index__c != index) {
                updates.put(p.Content__c, new Anime_Content__c(Id = p.Content__c, Performance_Index__c = index, Verdict__c = verdict, Scored_At__c = now));
            }
        }
        if (!updates.isEmpty()) {
            update updates.values();
        }
        return updates.size();
    }

    @TestVisible
    static Decimal index(Decimal views, Decimal watchMs, Decimal medViews, Decimal medWatch) {
        Decimal v = views / medViews;
        Decimal w = (watchMs != null && medWatch != null && medWatch > 0) ? watchMs / medWatch : v;
        return (0.6 * v + 0.4 * w).setScale(2);
    }

    @TestVisible
    static Decimal median(List<Decimal> values) {
        if (values.isEmpty()) return null;
        List<Decimal> s = values.clone();
        s.sort();
        Integer n = s.size();
        return Math.mod(n, 2) == 1 ? s[n / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
    }

    static Decimal ageHours(Datetime publishedAt, Datetime now) {
        return publishedAt == null ? 0 : Decimal.valueOf(now.getTime() - publishedAt.getTime()) / 3600000;
    }

    // ------------------------------------------------------------------ evidence

    /** Evidence for the agents and the site. show: optional filter. */
    public static Map<String, Object> insights(Datetime now, String show) {
        Integer dropAfter = Playbook.intValue('drop_after_losers', 2);
        Integer dropHours = Playbook.intValue('drop_hours', 48);
        List<Anime_Content__c> reels = [
            SELECT External_Id__c, CreatedDate, Slot__c, Anime__r.Name, Format__c, Topic__c, Verdict__c, Performance_Index__c,
                Showrunner_Directive__c, Scored_At__c, Music_Choice__c,
                (SELECT Views__c, Avg_Watch_Time_Ms__c, Published_At__c, Metrics_Updated_At__c FROM Social_Posts__r LIMIT 1)
            FROM Anime_Content__c
            WHERE Verdict__c != null AND CreatedDate >= :now.addDays(-30)
            ORDER BY CreatedDate DESC LIMIT 200
        ];
        Map<String, Map<String, Object>> byShow = new Map<String, Map<String, Object>>();
        Map<String, Map<String, Object>> byFormat = new Map<String, Map<String, Object>>();
        Map<String, Map<String, Object>> bySlot = new Map<String, Map<String, Object>>();
        Map<String, Map<String, Object>> byEffect = new Map<String, Map<String, Object>>();
        Map<String, Map<String, Object>> byStyle = new Map<String, Map<String, Object>>();
        Map<String, Map<String, Object>> byPlanner = new Map<String, Map<String, Object>>();
        Map<String, Map<String, Object>> byAudio = new Map<String, Map<String, Object>>();
        Map<String, List<Anime_Content__c>> showHistory = new Map<String, List<Anime_Content__c>>();
        List<String> tooYoung = new List<String>();
        Datetime freshest;
        for (Anime_Content__c c : reels) {
            String showName = c.Anime__r == null ? 'Unknown' : c.Anime__r.Name;
            if (String.isNotBlank(show) && !showName.containsIgnoreCase(show)) continue;
            if (!c.Social_Posts__r.isEmpty() && c.Social_Posts__r[0].Metrics_Updated_At__c != null
                    && (freshest == null || c.Social_Posts__r[0].Metrics_Updated_At__c > freshest)) {
                freshest = c.Social_Posts__r[0].Metrics_Updated_At__c;
            }
            if (c.Verdict__c == 'Too young') {
                tooYoung.add(c.External_Id__c + ' (' + showName + ')');
                continue;
            }
            if (c.Performance_Index__c == null) continue;
            if (!showHistory.containsKey(showName)) showHistory.put(showName, new List<Anime_Content__c>());
            showHistory.get(showName).add(c);
            add(byShow, showName, c);
            add(byFormat, c.Format__c, c);
            add(bySlot, c.Slot__c, c);
            add(byAudio, c.Music_Choice__c != null && c.Music_Choice__c.startsWith('Instagram trending audio') ? 'Instagram trending audio' : 'library music', c);
            Map<String, Object> directive = parse(c.Showrunner_Directive__c);
            add(byPlanner, directive.isEmpty() ? 'default plan (no agent)' : String.valueOf(directive.get('by')), c);
            Object style = directive.get('style');
            if (style instanceof Map<String, Object>) {
                for (String k : new List<String>{ 'transition', 'grading', 'caption_mode' }) {
                    Object v = ((Map<String, Object>) style).get(k);
                    if (v != null) add(byStyle, k + '=' + v, c);
                }
                Object pace = ((Map<String, Object>) style).get('image_interval');
                if (pace != null) add(byStyle, Decimal.valueOf(String.valueOf(pace)) < 2.2 ? 'pace=fast cuts' : 'pace=slow cuts', c);
            }
            Object edits = directive.get('edits');
            if (edits instanceof Map<String, Object>) {
                Set<String> used = new Set<String>();
                for (Object items : ((Map<String, Object>) edits).values()) {
                    if (!(items instanceof List<Object>)) continue;
                    for (Object item : (List<Object>) items) {
                        String fx = item instanceof Map<String, Object> ? String.valueOf(((Map<String, Object>) item).get('fx')) : String.valueOf(item);
                        if (fx != null) used.add(fx);
                    }
                }
                for (String fx : used) add(byEffect, fx, c);
            }
        }
        List<Map<String, Object>> shows = finish(byShow);
        for (Map<String, Object> s : shows) {
            List<Anime_Content__c> hist = showHistory.get((String) s.get('name'));
            List<String> last = new List<String>();
            for (Integer i = 0; i < Math.min(3, hist.size()); i++) last.add(hist[i].Verdict__c);
            s.put('last_verdicts', last);
            Boolean dropped = hist.size() >= dropAfter;
            for (Integer i = 0; dropped && i < dropAfter; i++) dropped = hist[i].Verdict__c == 'Loser';
            Datetime until = hist.isEmpty() ? null : hist[0].CreatedDate.addHours(dropHours);
            if (dropped && until > now) {
                s.put('status', 'DROPPED until ' + until.format('EEE HH:mm', InstaAgentConfig.TIMEZONE_ID) + ' IST (last ' + dropAfter + ' were losers)');
            } else if (!last.isEmpty() && last[0] == 'Winner') {
                s.put('status', 'RIDE IT (last Reel was a winner)');
            } else {
                s.put('status', 'open');
            }
        }
        // fact inventory: verified facts per show vs how often we covered it lately (a show runs dry when overused)
        Map<String, Integer> facts = new Map<String, Integer>();
        for (AggregateResult ar : [SELECT Anime__r.Name n, COUNT(Id) c FROM Anime_Knowledge__c WHERE Verified__c = true
                                   AND Anime__c != null GROUP BY Anime__r.Name]) {
            facts.put(String.valueOf(ar.get('n')).toLowerCase(), (Integer) ar.get('c'));
        }
        Map<String, Integer> recent3d = new Map<String, Integer>();
        for (AggregateResult ar : [SELECT Anime__r.Name n, COUNT(Id) c FROM Anime_Content__c WHERE CreatedDate >= :now.addDays(-3)
                                   AND Anime__c != null GROUP BY Anime__r.Name]) {
            recent3d.put(String.valueOf(ar.get('n')).toLowerCase(), (Integer) ar.get('c'));
        }
        List<Map<String, Object>> inventory = new List<Map<String, Object>>();
        for (String name : facts.keySet()) {
            Integer f = facts.get(name), used = recent3d.containsKey(name) ? recent3d.get(name) : 0;
            inventory.add(new Map<String, Object>{ 'show' => name, 'verified_facts' => f, 'reels_last_3_days' => used,
                'risk' => f < 6 ? 'THIN: too few facts for most formats - prefer news formats or another show'
                    : (used * 3 >= f ? 'RUNNING DRY: covered a lot lately - new angle needed or rest it' : 'ok') });
        }
        for (Map<String, Object> sh : shows) {
            String key = String.valueOf(sh.get('name')).toLowerCase();
            sh.put('verified_facts', facts.containsKey(key) ? facts.get(key) : 0);
        }
        Decimal freshHours = freshest == null ? null : (Decimal.valueOf(now.getTime() - freshest.getTime()) / 3600000).setScale(1);
        return new Map<String, Object>{
            'how_to_read' => 'performance index 1.00 = a normal Reel for this account (views 60%, watch time 40%). Winner >= 1.25, loser <= 0.75. '
                + 'Reels younger than ' + MIN_AGE_HOURS + ' h are too young to judge. n = scored Reels; small n is weak evidence.',
            'scored_reels' => byPlanner.isEmpty() ? 0 : count(byPlanner),
            'too_young_to_judge' => tooYoung,
            'metrics_freshness_hours' => freshHours,
            'by_show' => shows, 'by_format' => finish(byFormat), 'by_slot' => finish(bySlot),
            'by_effect' => finish(byEffect), 'by_audio' => finish(byAudio), 'by_style' => finish(byStyle), 'by_planner' => finish(byPlanner),
            'fact_inventory' => inventory,
            'drop_rule' => 'drop a show after ' + dropAfter + ' losers in a row, for ' + dropHours + ' h (playbook)'
        };
    }

    static void add(Map<String, Map<String, Object>> groups, String key, Anime_Content__c c) {
        key = String.isBlank(key) ? 'unknown' : key;
        if (!groups.containsKey(key)) {
            groups.put(key, new Map<String, Object>{ 'name' => key, 'n' => 0, 'sum' => 0.0, 'wins' => 0, 'losses' => 0 });
        }
        Map<String, Object> g = groups.get(key);
        g.put('n', (Integer) g.get('n') + 1);
        g.put('sum', (Decimal) g.get('sum') + c.Performance_Index__c);
        if (c.Verdict__c == 'Winner') g.put('wins', (Integer) g.get('wins') + 1);
        if (c.Verdict__c == 'Loser') g.put('losses', (Integer) g.get('losses') + 1);
    }

    static List<Map<String, Object>> finish(Map<String, Map<String, Object>> groups) {
        List<Map<String, Object>> out = new List<Map<String, Object>>();
        for (Map<String, Object> g : groups.values()) {
            g.put('avg_index', ((Decimal) g.get('sum') / (Integer) g.get('n')).setScale(2));
            g.remove('sum');
            out.add(g);
        }
        // best first (simple insertion sort - lists are small)
        for (Integer i = 1; i < out.size(); i++) {
            Map<String, Object> x = out[i];
            Integer j = i - 1;
            while (j >= 0 && (Decimal) out[j].get('avg_index') < (Decimal) x.get('avg_index')) {
                out[j + 1] = out[j];
                j--;
            }
            out[j + 1] = x;
        }
        return out;
    }

    static Integer count(Map<String, Map<String, Object>> groups) {
        Integer n = 0;
        for (Map<String, Object> g : groups.values()) n += (Integer) g.get('n');
        return n;
    }

    /** Average index of the last n scored Reels created before / after a moment (null when none). */
    public static Decimal averageIndex(Datetime before, Integer n) {
        return avg([SELECT Performance_Index__c FROM Anime_Content__c WHERE Performance_Index__c != null AND CreatedDate < :before
                    ORDER BY CreatedDate DESC LIMIT :n]);
    }

    public static List<Anime_Content__c> scoredSince(Datetime after) {
        return [SELECT Performance_Index__c FROM Anime_Content__c WHERE Performance_Index__c != null AND CreatedDate > :after
                ORDER BY CreatedDate LIMIT 50];
    }

    public static Decimal avg(List<Anime_Content__c> rows) {
        if (rows.isEmpty()) return null;
        Decimal sum = 0;
        for (Anime_Content__c c : rows) sum += c.Performance_Index__c;
        return (sum / rows.size()).setScale(2);
    }

    static Map<String, Object> parse(String value) {
        Object o = ShowrunnerSupport.parse(value);
        return o instanceof Map<String, Object> ? (Map<String, Object>) o : new Map<String, Object>();
    }
}
