/**
 * POST /services/apexrest/insta/studio/task  {"system": "...", "task": "..."}
 * Generic JSON task on Einstein (fact extraction / verification for the pipeline).
 */
@RestResource(urlMapping='/insta/studio/task')
global with sharing class StudioTaskResource {
    @HttpPost
    global static void run() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        try {
            Map<String, Object> request = (Map<String, Object>) JSON.deserializeUntyped(RestContext.request.requestBody.toString());
            if (String.isBlank((String) request.get('task'))) {
                res.statusCode = 400;
                res.responseBody = Blob.valueOf('{"error":"task is required"}');
                return;
            }
            Map<String, Object> result = PromptRunner.runJson('Insta_JSON_Task', request);
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(result));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() }));
        }
    }
}
