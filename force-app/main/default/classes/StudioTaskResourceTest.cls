@IsTest
private class StudioTaskResourceTest {
    @IsTest
    static void runsTaskAndValidates() {
        PromptRunner.mock = new Map<String, List<String>>{ 'Insta_JSON_Task' => new List<String>{ 'Sure: {"facts": []}' } };
        RestContext.response = new RestResponse();
        RestContext.request = new RestRequest();
        RestContext.request.requestBody = Blob.valueOf('{"system":"extract","task":"text"}');
        StudioTaskResource.run();
        Assert.areEqual(200, RestContext.response.statusCode);
        Assert.areEqual('{"facts":[]}', RestContext.response.responseBody.toString());

        RestContext.response = new RestResponse();
        RestContext.request.requestBody = Blob.valueOf('{"system":"x"}');
        StudioTaskResource.run();
        Assert.areEqual(400, RestContext.response.statusCode);

        RestContext.response = new RestResponse();
        RestContext.request.requestBody = Blob.valueOf('{"system":"x","task":"y"}');
        StudioTaskResource.run(); // no mock reply left -> 500
        Assert.areEqual(500, RestContext.response.statusCode);
    }
}
