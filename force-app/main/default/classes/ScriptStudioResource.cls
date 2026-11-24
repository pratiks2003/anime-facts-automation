/**
 * POST /services/apexrest/insta/studio/script
 * Called by the GitHub pipeline (integration user) with the planned facts; returns the
 * agent-written, director-approved, fact-checked script plus scores and production brief.
 */
@RestResource(urlMapping='/insta/studio/script')
global with sharing class ScriptStudioResource {
    @HttpPost
    global static void run() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        try {
            Map<String, Object> request = (Map<String, Object>) JSON.deserializeUntyped(RestContext.request.requestBody.toString());
            if (request.get('facts') == null || request.get('format') == null) {
                res.statusCode = 400;
                res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => 'format and facts are required' }));
                return;
            }
            ScriptStudio.Result result = ScriptStudio.run(request);
            try {
                ScriptStudio.record((String) request.get('content_id'), result);
            } catch (Exception e) {
                System.debug(LoggingLevel.WARN, 'Could not record quality: ' + e.getMessage());
            }
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(result.toMap()));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(
                JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() })
            );
        }
    }
}
