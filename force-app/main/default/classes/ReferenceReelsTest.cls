@IsTest
private class ReferenceReelsTest {
    private class GitHubOk implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest req) {
            Assert.isTrue(req.getEndpoint().endsWith('/actions/workflows/analyze.yml/dispatches'));
            Assert.isTrue(req.getBody().contains('reference_id'));
            HttpResponse res = new HttpResponse();
            res.setStatusCode(204);
            return res;
        }
    }

    @IsTest
    static void urlValidation() {
        Assert.isTrue(ReferenceReels.validUrl('https://www.instagram.com/reel/DcQ8dDKRFrF/?igsh=abc'));
        Assert.isTrue(ReferenceReels.validUrl('https://youtube.com/shorts/abc_123'));
        Assert.isFalse(ReferenceReels.validUrl('https://example.com/reel/x'));
        Assert.isFalse(ReferenceReels.validUrl(null));
    }

    @IsTest
    static void addReferenceDispatchesWorkflow() {
        InstaAgentTestUtil.settings(true, false);
        Test.setMock(HttpCalloutMock.class, new GitHubOk());
        Test.startTest();
        Id refId = AdminPanelController.addReference('https://www.instagram.com/reel/DcQ8dDKRFrF/', 'love the pacing');
        Test.stopTest();
        Assert.areEqual('Analyzing', [SELECT Status__c FROM Reference_Reel__c WHERE Id = :refId].Status__c);
        try {
            AdminPanelController.addReference('not a link', null);
            Assert.fail('invalid link accepted');
        } catch (AuraHandledException e) {
            Assert.isTrue(e.getMessage().contains('public'));
        }
    }

    @IsTest
    static void callbackBuildsLessonsAndStyle() {
        InstaAgentTestUtil.settings(true, false);
        Reference_Reel__c ref = new Reference_Reel__c(Name = 'x', URL__c = 'https://www.instagram.com/reel/abc/', Status__c = 'Analyzing');
        insert ref;
        PromptRunner.mock = new Map<String, List<String>>{
            ReferenceReels.TEMPLATE => new List<String>{
                '{"summary":"A lore deep-dive with 2s cuts.","lessons":["Open on the payoff","Cut every 2s"],"hook_formula":"X was revealed N chapters ago",' +
                '"style":{"name":"Lore Rush","why":"fast lore","spec":{"image_interval":1.9,"voice_speed":1.35}}}'
            }
        };
        RestContext.request = new RestRequest();
        RestContext.response = new RestResponse();
        RestContext.request.requestBody = Blob.valueOf(JSON.serialize(new Map<String, Object>{
            'id' => ref.Id, 'status' => 'Measured',
            'analysis' => new Map<String, Object>{ 'duration' => 110, 'cut_interval_seconds' => 1.83, 'transcript' => 'Imu plan...', 'meta' => new Map<String, Object>{ 'uploader' => 'Some One', 'channel' => 'lore.page' } }
        }));
        ReferenceReelResource.run();
        Assert.areEqual(200, RestContext.response.statusCode, RestContext.response.responseBody.toString());
        Reference_Reel__c done = [SELECT Status__c, Lessons__c, Style__r.Name, Style__r.Origin__c, Style__r.Status__c FROM Reference_Reel__c WHERE Id = :ref.Id];
        Assert.areEqual('Done', done.Status__c);
        Assert.isTrue(done.Lessons__c.contains('Hook formula'));
        Assert.areEqual('Lore Rush', done.Style__r.Name);
        Assert.areEqual('Reference', done.Style__r.Origin__c);
        Assert.areEqual('Testing', done.Style__r.Status__c);

        Assert.isTrue(InstaAgent_Settings__c.getOrgDefaults().Watchlist__c.contains('lore.page'), 'author is watched');
        Map<String, Object> research = AdminPanelController.getResearch();
        Assert.areEqual(1, ((List<Object>) research.get('references')).size());
        AdminPanelController.setStyleStatus(done.Style__c, 'Active');
        Assert.areEqual('Active', [SELECT Status__c FROM Style__c WHERE Id = :done.Style__c].Status__c);
    }

    @IsTest
    static void failureCallbackMarksFailed() {
        Reference_Reel__c ref = new Reference_Reel__c(Name = 'x', URL__c = 'https://www.instagram.com/reel/abc/', Status__c = 'Analyzing');
        insert ref;
        Map<String, Object> out = ReferenceReels.complete(new Map<String, Object>{ 'id' => ref.Id, 'status' => 'Failed', 'error' => 'login required' });
        Assert.areEqual('Failed', out.get('status'));
        Assert.areEqual('login required', [SELECT Error__c FROM Reference_Reel__c WHERE Id = :ref.Id].Error__c);
    }

    @IsTest
    static void badCallbackReturns500() {
        RestContext.request = new RestRequest();
        RestContext.response = new RestResponse();
        RestContext.request.requestBody = Blob.valueOf('{"id":"not-an-id","status":"Measured"}');
        ReferenceReelResource.run();
        Assert.areEqual(500, RestContext.response.statusCode);
    }
}
