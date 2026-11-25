/**
 * POST /services/apexrest/insta/studio/revise
 * {"content_id", "format", "anime", "facts": [...], "script": {...}, "issues": ["too long (52s)", ...]}
 * Post-render QA problems go back to the team: Director fixes, facts repaired, caption rewritten.
 */
@RestResource(urlMapping='/insta/studio/revise')
global with sharing class StudioReviseResource {
    @HttpPost
    global static void run() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        try {
            Map<String, Object> request = (Map<String, Object>) JSON.deserializeUntyped(RestContext.request.requestBody.toString());
            if (!(request.get('script') instanceof Map<String, Object>) || !(request.get('issues') instanceof List<Object>)) {
                res.statusCode = 400;
                res.responseBody = Blob.valueOf('{"error":"script and issues are required"}');
                return;
            }
            ScriptStudio.Result result = ScriptStudio.revise(request);
            try {
                ScriptStudio.record((String) request.get('content_id'), result);
            } catch (Exception e) {
                System.debug(LoggingLevel.WARN, 'Could not record quality: ' + e.getMessage());
            }
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(result.toMap()));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() }));
        }
    }
}
