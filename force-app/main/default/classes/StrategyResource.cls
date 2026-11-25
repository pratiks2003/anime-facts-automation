/**
 * POST /services/apexrest/insta/studio/strategy  -> Creative Strategist decision for a Reel.
 */
@RestResource(urlMapping='/insta/studio/strategy')
global with sharing class StrategyResource {
    @HttpPost
    global static void run() {
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');
        try {
            Map<String, Object> request = (Map<String, Object>) JSON.deserializeUntyped(RestContext.request.requestBody.toString());
            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(CreativeStrategist.decide(request)));
        } catch (Exception e) {
            res.statusCode = 500;
            res.responseBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{ 'error' => e.getTypeName() + ': ' + e.getMessage() }));
        }
    }
}
