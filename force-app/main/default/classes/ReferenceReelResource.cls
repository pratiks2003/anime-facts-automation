/**
 * POST /services/apexrest/insta/research/reference  (callback from the analyze.yml GitHub workflow)
 */
@RestResource(urlMapping='/insta/research/reference')
global with sharing class ReferenceReelResource {
    @HttpPost
    global static void run() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        try {
            Map<String, Object> body = (Map<String, Object>) JSON.deserializeUntyped(RestContext.request.requestBody.toString());
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(ReferenceReels.complete(body)));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() }));
        }
    }
}
