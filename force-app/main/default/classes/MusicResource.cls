/**
 * POST /services/apexrest/insta/studio/music
 * {"brief": {music_mood, music_energy, music_keywords, format, video_seconds}, "candidates": [{id, title, tags, duration_seconds}]}
 * Music Supervisor agent picks the best licensed track for the Director's brief.
 */
@RestResource(urlMapping='/insta/studio/music')
global with sharing class MusicResource {
    /** Titles of tracks the owner rated (Music_Choice__c starts with "<title> by <artist>"). */
    public static Map<String, List<String>> taste() {
        Map<String, List<String>> out = new Map<String, List<String>>{ 'liked' => new List<String>(), 'disliked' => new List<String>() };
        for (Anime_Content__c c : [
            SELECT Music_Choice__c, Music_Rating__c FROM Anime_Content__c
            WHERE Music_Rating__c != null AND Music_Choice__c != null ORDER BY LastModifiedDate DESC LIMIT 40
        ]) {
            String title = c.Music_Choice__c.substringBefore(' by ').trim();
            out.get(c.Music_Rating__c == 'Good' ? 'liked' : 'disliked').add(title);
        }
        return out;
    }

    @HttpPost
    global static void run() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        try {
            Map<String, Object> request = (Map<String, Object>) JSON.deserializeUntyped(RestContext.request.requestBody.toString());
            List<Object> candidates = (List<Object>) request.get('candidates');
            if (candidates == null || candidates.isEmpty()) {
                res.statusCode = 400;
                res.responseBody = Blob.valueOf('{"error":"candidates are required"}');
                return;
            }
            Map<String, List<String>> taste = taste();
            Set<String> disliked = new Set<String>();
            for (String t : taste.get('disliked')) {
                disliked.add(t.toLowerCase());
            }
            List<Object> allowed = new List<Object>();
            Set<String> ids = new Set<String>();
            for (Object o : candidates) {
                Map<String, Object> c = (Map<String, Object>) o;
                if (disliked.contains(String.valueOf(c.get('title')).toLowerCase())) {
                    continue; // never repeat a track the owner rated bad
                }
                allowed.add(c);
                ids.add(String.valueOf(c.get('id')));
            }
            if (allowed.isEmpty()) {
                res.statusCode = 200;
                res.responseBody = Blob.valueOf('{"pick":null,"reason":"all candidates were previously rated bad"}');
                return;
            }
            request.put('candidates', allowed);
            request.put('taste', taste);
            Map<String, Object> out = PromptRunner.runJson('Insta_Music_Supervisor', request);
            if (out.get('pick') == null) {
                res.statusCode = 200;
                res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'pick' => null, 'reason' => out.get('reason') }));
                return;
            }
            String pick = String.valueOf(out.get('pick'));
            if (!ids.contains(pick)) {
                res.statusCode = 422;
                res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => 'agent picked an unknown candidate', 'pick' => pick }));
                return;
            }
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'pick' => pick, 'reason' => out.get('reason') }));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() }));
        }
    }
}
