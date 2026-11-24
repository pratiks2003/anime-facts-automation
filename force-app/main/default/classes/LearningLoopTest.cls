@IsTest
private class LearningLoopTest {
    static final String ANALYST = '{"summary":"JoJo hooks win.","best_formats":["Quiz"],"weak_formats":["Fact"],"best_slot":"Evening",'
        + '"feature_anime":["JoJo"],"avoid_anime":["Black Clover"],"hook_lessons":["Name the payoff","Lead with the biggest show"],'
        + '"topic_ideas":[{"anime":"JoJo","format":"Quiz","idea":"Guess the Stand from 3 clues"},{"anime":"X","format":"Nope","idea":"Bad format idea"}]}';

    @IsTest
    static void analystWritesReviewAndBacklog() {
        Anime_Content__c c = new Anime_Content__c(Name = 'R', External_Id__c = 'R-A', Format__c = 'Quiz', Status__c = 'Published', Quality_Score__c = 8.7);
        insert c;
        insert new Social_Post__c(Content__c = c.Id, Instagram_Media_Id__c = 'M-1', Likes__c = 10, Status__c = 'Published');
        PromptRunner.mock = new Map<String, List<String>>{ PerformanceAnalyst.TEMPLATE => new List<String>{ ANALYST } };
        Test.startTest();
        Performance_Review__c r = PerformanceAnalyst.run(Date.today());
        Test.stopTest();
        r = [SELECT Feature_Anime__c, Avoid_Anime__c, Hook_Lessons__c, Reels_Analysed__c FROM Performance_Review__c WHERE Id = :r.Id];
        Assert.areEqual('JoJo', r.Feature_Anime__c);
        Assert.areEqual(1, r.Reels_Analysed__c);
        Assert.isTrue(r.Hook_Lessons__c.contains('Name the payoff'));
        List<Topic_Idea__c> ideas = [SELECT Name, Format__c, Source__c FROM Topic_Idea__c ORDER BY Name];
        Assert.areEqual(2, ideas.size());
        Assert.isNull(ideas[0].Format__c, 'unknown formats are dropped, idea kept');
        Assert.areEqual('Analyst', ideas[1].Source__c);
        Assert.areEqual(0, TopicBacklog.add(JSON.deserializeUntyped('[{"anime":"JoJo","format":"Quiz","idea":"Guess the Stand from 3 clues"}]'), 'Admin'), 'duplicates skipped');
    }

    @IsTest
    static void schedulesWeekly() {
        PromptRunner.mock = new Map<String, List<String>>{ PerformanceAnalyst.TEMPLATE => new List<String>{ ANALYST, ANALYST } };
        Test.startTest();
        PerformanceAnalyst.scheduleWeekly();
        PerformanceAnalyst.scheduleWeekly();
        Test.stopTest();
        Assert.areEqual(1, [SELECT COUNT() FROM CronTrigger WHERE CronJobDetail.Name = :PerformanceAnalyst.JOB_NAME]);
    }

    @IsTest
    static void communityManagerRepliesToAllAndSkipsUnsafe() {
        insert new Social_Post__c(Instagram_Media_Id__c = 'M-2', Status__c = 'Published');
        InstaAgentTestUtil.settings(true, false);
        InstaAgent_Settings__c s = InstaAgent_Settings__c.getOrgDefaults();
        s.Auto_Reply_Comments__c = true;
        update s;
        InstaAgentConfig.cached = null;
        PromptRunner.mock = new Map<String, List<String>>{
            'Insta_Community_Manager' => new List<String>{ '{"replies":['
                + '{"comment_id":"c1","category":"request","reply":"Zoro Reel loading 👀"},'
                + '{"comment_id":"c2","category":"insult","reply":"Ouch! Senpai survived the filler arcs 😤"},'
                + '{"comment_id":"c3","category":"spam","reply":""},'
                + '{"comment_id":"zzz","category":"other","reply":"hallucinated id"}],'
                + '"topic_ideas":[{"anime":"One Piece","format":"Hidden Details","idea":"Zoro details you missed"}]}' }
        };
        String body = '{"media_id":"M-2","reel":{"format":"Quiz"},"comments":[{"id":"c1","text":"Do Zoro next!"},{"id":"c2","text":"this is trash"},{"id":"c3","text":"follow me for crypto"}]}';
        RestContext.response = new RestResponse();
        RestContext.request = new RestRequest();
        RestContext.request.requestBody = Blob.valueOf(body);
        CommunityResource.run();
        Assert.areEqual(200, RestContext.response.statusCode);
        Map<String, Object> out = (Map<String, Object>) JSON.deserializeUntyped(RestContext.response.responseBody.toString());
        Assert.areEqual(2, ((List<Object>) out.get('replies')).size(), 'request + insult joke; spam skipped');
        Assert.areEqual(3, [SELECT COUNT() FROM Comment_Reply__c]);
        Assert.areEqual('Skipped', [SELECT Status__c FROM Comment_Reply__c WHERE Comment_Id__c = 'c3'].Status__c);
        Assert.areEqual('Comments', [SELECT Source__c FROM Topic_Idea__c].Source__c);

        // second run: nothing new to answer, no agent call needed
        RestContext.response = new RestResponse();
        CommunityResource.run();
        out = (Map<String, Object>) JSON.deserializeUntyped(RestContext.response.responseBody.toString());
        Assert.areEqual(0, ((List<Object>) out.get('replies')).size());

        RestContext.response = new RestResponse();
        RestContext.request.requestBody = Blob.valueOf('{"media_id":"M-2","comments":[]}');
        CommunityResource.run();
        Assert.areEqual(400, RestContext.response.statusCode);
    }
}
