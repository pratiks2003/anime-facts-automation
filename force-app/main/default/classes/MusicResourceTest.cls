@IsTest
private class MusicResourceTest {
    static void call(String body) {
        RestContext.response = new RestResponse();
        RestContext.request = new RestRequest();
        RestContext.request.requestBody = Blob.valueOf(body);
        MusicResource.run();
    }

    @IsTest
    static void picksKnownCandidateOnly() {
        String body = '{"brief":{"music_mood":"hype"},"candidates":[{"id":"a1","title":"Epic Rise"},{"id":"b2","title":"Lofi"}]}';
        PromptRunner.mock = new Map<String, List<String>>{
            'Insta_Music_Supervisor' => new List<String>{ '{"pick":"a1","reason":"epic and long enough"}', '{"pick":"zzz","reason":"made up"}' }
        };
        call(body);
        Assert.areEqual(200, RestContext.response.statusCode);
        Assert.isTrue(RestContext.response.responseBody.toString().contains('"pick":"a1"'));
        call(body);
        Assert.areEqual(422, RestContext.response.statusCode);
        call('{"brief":{},"candidates":[]}');
        Assert.areEqual(400, RestContext.response.statusCode);
    }
}
