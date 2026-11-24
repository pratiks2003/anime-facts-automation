/**
 * POST /services/apexrest/insta/studio/music
 * {"brief": {music_mood, music_energy, music_keywords, format, video_seconds}, "candidates": [{id, title, tags, duration_seconds}]}
 * Music Supervisor agent picks the best licensed track for the Director's brief.
 */
@RestResource(urlMapping='/insta/studio/music')
global with sharing class MusicResource {
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
            Set<String> ids = new Set<String>();
            for (Object o : candidates) {
                ids.add(String.valueOf(((Map<String, Object>) o).get('id')));
            }
            Map<String, Object> out = PromptRunner.runJson('Insta_Music_Supervisor', request);
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
