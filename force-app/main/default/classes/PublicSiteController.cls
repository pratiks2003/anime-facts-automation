/**
 * Backend for the PUBLIC "Senpai Secrets" site (guest users).
 * Runs without sharing because guests have no record access; every method therefore
 * selects explicit, non-sensitive fields and only published / verified / curated data.
 */
public without sharing class PublicSiteController {
    private static final Integer MAX_IDEA_LENGTH = 140;

    @AuraEnabled(cacheable=true)
    public static List<Map<String, Object>> latestReels(Integer limitSize) {
        Integer n = limitSize == null ? 6 : Math.max(1, Math.min(limitSize, 12));
        List<Map<String, Object>> out = new List<Map<String, Object>>();
        for (Social_Post__c p : [
            SELECT Permalink__c, Published_At__c, Content__r.Name, Content__r.Format__c, Content__r.Anime__r.Name
            FROM Social_Post__c
            WHERE Status__c = 'Published' AND Permalink__c != null
            ORDER BY Published_At__c DESC
            LIMIT :n
        ]) {
            out.add(new Map<String, Object>{
                'permalink' => p.Permalink__c, 'publishedAt' => p.Published_At__c,
                'title' => p.Content__r == null ? null : p.Content__r.Name,
                'format' => p.Content__r == null ? null : p.Content__r.Format__c,
                'anime' => p.Content__r == null || p.Content__r.Anime__r == null ? null : p.Content__r.Anime__r.Name
            });
        }
        return out;
    }

    /** Popular episodes airing in the next 7 days, from AniList (times in IST). */
    @AuraEnabled(cacheable=true)
    public static List<Map<String, Object>> weeklyDrops() {
        Long nowSec = Datetime.now().getTime() / 1000;
        String query = 'query ($from: Int, $to: Int) { Page(perPage: 50) { airingSchedules(airingAt_greater: $from, airingAt_lesser: $to, sort: TIME) {'
            + ' episode airingAt media { id isAdult popularity averageScore siteUrl title { english romaji } coverImage { large } } } } }';
        HttpRequest req = new HttpRequest();
        req.setEndpoint('https://graphql.anilist.co');
        req.setMethod('POST');
        req.setHeader('Content-Type', 'application/json');
        req.setTimeout(20000);
        req.setBody(JSON.serialize(new Map<String, Object>{
            'query' => query, 'variables' => new Map<String, Object>{ 'from' => nowSec, 'to' => nowSec + 7 * 86400 }
        }));
        HttpResponse res = new Http().send(req);
        if (res.getStatusCode() != 200) {
            throw userError('Anime schedule is unavailable right now.');
        }
        Map<String, Object> data = (Map<String, Object>) ((Map<String, Object>) JSON.deserializeUntyped(res.getBody())).get('data');
        List<Object> schedules = (List<Object>) ((Map<String, Object>) data.get('Page')).get('airingSchedules');
        Map<Integer, Map<String, Object>> byShow = new Map<Integer, Map<String, Object>>();
        for (Object o : schedules) {
            Map<String, Object> s = (Map<String, Object>) o;
            Map<String, Object> m = (Map<String, Object>) s.get('media');
            if (m == null || m.get('isAdult') == true) {
                continue;
            }
            Integer id = (Integer) m.get('id');
            if (byShow.containsKey(id)) {
                continue; // first (earliest) episode this week
            }
            Map<String, Object> t = (Map<String, Object>) m.get('title');
            Datetime airs = Datetime.newInstance(((Long) (Integer) s.get('airingAt')) * 1000);
            byShow.put(id, new Map<String, Object>{
                'title' => t.get('english') != null ? t.get('english') : t.get('romaji'),
                'episode' => s.get('episode'),
                'airs' => airs.format('EEE d MMM, h:mm a', 'Asia/Kolkata') + ' IST',
                'airsAt' => airs,
                'popularity' => m.get('popularity'),
                'score' => m.get('averageScore'),
                'poster' => ((Map<String, Object>) m.get('coverImage')).get('large'),
                'url' => m.get('siteUrl')
            });
        }
        List<Map<String, Object>> shows = byShow.values();
        // top 8 by popularity, then shown in airing order
        List<Integer> pops = new List<Integer>();
        for (Map<String, Object> s : shows) {
            pops.add(s.get('popularity') == null ? 0 : (Integer) s.get('popularity'));
        }
        pops.sort();
        Integer cutoff = pops.size() > 8 ? pops[pops.size() - 8] : 0;
        List<Map<String, Object>> top = new List<Map<String, Object>>();
        for (Map<String, Object> s : shows) {
            if ((s.get('popularity') == null ? 0 : (Integer) s.get('popularity')) >= cutoff && top.size() < 8) {
                top.add(s);
            }
        }
        return top;
    }

    /** Verified, low-spoiler facts from the public GitHub knowledge base. */
    @AuraEnabled(cacheable=true)
    public static List<Map<String, Object>> facts() {
        String base = 'https://raw.githubusercontent.com/' + InstaAgentConfig.repository() + '/main/knowledge/';
        Map<String, Object> meta = (Map<String, Object>) JSON.deserializeUntyped(fetch(base + 'anime_metadata.json'));
        List<Map<String, Object>> out = new List<Map<String, Object>>();
        Integer files = 0;
        for (Object o : (List<Object>) meta.get('anime')) {
            Map<String, Object> entry = (Map<String, Object>) o;
            if (files++ >= 12 || entry.get('knowledge_file') == null) {
                continue;
            }
            String body;
            try {
                body = fetch(base + (String) entry.get('knowledge_file'));
            } catch (Exception e) {
                continue; // anime registered but no facts file yet
            }
            for (String line : body.split('\n')) {
                if (String.isBlank(line)) {
                    continue;
                }
                Map<String, Object> r = (Map<String, Object>) JSON.deserializeUntyped(line);
                String spoiler = (String) r.get('spoiler_level');
                if (r.get('verified') == true && (spoiler == 'none' || spoiler == 'low') && r.get('canon_status') != 'theory') {
                    out.add(new Map<String, Object>{
                        'id' => r.get('id'), 'anime' => r.get('anime'), 'title' => r.get('title'),
                        'content' => r.get('content'), 'source' => r.get('source_url'), 'category' => r.get('category')
                    });
                }
            }
        }
        return out;
    }

    @AuraEnabled(cacheable=true)
    public static List<Map<String, Object>> ideas() {
        List<Map<String, Object>> out = new List<Map<String, Object>>();
        for (Topic_Idea__c t : [
            SELECT Id, Name, Anime__c, Format__c, Votes__c FROM Topic_Idea__c WHERE Status__c = 'New' ORDER BY Votes__c DESC NULLS LAST, CreatedDate DESC LIMIT 12
        ]) {
            out.add(new Map<String, Object>{ 'id' => t.Id, 'idea' => t.Name, 'anime' => t.Anime__c, 'format' => t.Format__c, 'votes' => t.Votes__c == null ? 0 : t.Votes__c });
        }
        return out;
    }

    @AuraEnabled
    public static Integer vote(String ideaId) {
        Id parsed;
        try {
            parsed = Id.valueOf(ideaId);
        } catch (Exception e) {
            throw userError('Unknown idea.');
        }
        if (parsed.getSobjectType() != Topic_Idea__c.SObjectType) {
            throw userError('Unknown idea.');
        }
        List<Topic_Idea__c> rows = [SELECT Id, Votes__c FROM Topic_Idea__c WHERE Id = :parsed AND Status__c = 'New' LIMIT 1 FOR UPDATE];
        if (rows.isEmpty()) {
            throw userError('This idea is no longer open for votes.');
        }
        rows[0].Votes__c = (rows[0].Votes__c == null ? 0 : rows[0].Votes__c) + 1;
        update rows;
        return rows[0].Votes__c.intValue();
    }

    /** Public suggestions wait as "Suggested" until the admin approves them into the backlog. */
    @AuraEnabled
    public static void suggest(String anime, String idea) {
        String cleanIdea = clean(idea, MAX_IDEA_LENGTH);
        if (cleanIdea.length() < 8) {
            throw userError('Tell us a little more about your idea (8+ characters).');
        }
        if ([SELECT COUNT() FROM Topic_Idea__c WHERE Status__c = 'Suggested' AND CreatedDate = LAST_N_DAYS:1] >= 50) {
            throw userError('Lots of suggestions today - please try again tomorrow!');
        }
        insert new Topic_Idea__c(Name = cleanIdea.abbreviate(80), Anime__c = clean(anime, 120), Source__c = 'Public Vote', Status__c = 'Suggested', Votes__c = 1);
    }

    private static AuraHandledException userError(String message) {
        AuraHandledException e = new AuraHandledException(message);
        e.setMessage(message); // otherwise Apex-side getMessage() is "Script-thrown exception"
        return e;
    }

    private static String clean(String value, Integer maxLength) {
        String s = value == null ? '' : value.stripHtmlTags().normalizeSpace();
        return s.left(maxLength);
    }

    private static String fetch(String url) {
        HttpRequest req = new HttpRequest();
        req.setEndpoint(url);
        req.setMethod('GET');
        req.setTimeout(15000);
        HttpResponse res = new Http().send(req);
        if (res.getStatusCode() != 200) {
            throw new CalloutException('HTTP ' + res.getStatusCode() + ' for ' + url);
        }
        return res.getBody();
    }
}
