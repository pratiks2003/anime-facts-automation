@IsTest
private class ShowrunnerActionsTest {
    @TestSetup
    static void data() {
        InstaAgentTestUtil.settings(true, true);
        insert new Anime_Content__c(Name = 'R1', External_Id__c = 'SR-1', Format__c = 'Quiz', Status__c = 'Rendered',
            Approval_Status__c = 'Pending', Hold_Reason__c = 'Quality score 7.5 is below the bar of 8',
            Render_Report__c = '{"visual_qa":{"edit_score":8,"issues":[]},"fact_check":{"pass":true}}',
            Script_JSON__c = '{"scenes":[{"role":"hook","narration":"Hi"}]}');
        insert new Anime_Content__c(Name = 'R2', External_Id__c = 'SR-2', Format__c = 'Fact', Status__c = 'Rendered',
            Approval_Status__c = 'Pending', Render_Report__c = '{"visual_qa":{"edit_score":5}}');
        insert new Trend_Brief__c(Name = 'b', Brief_Date__c = Date.today(), Summary__c = 's',
            Brief_JSON__c = '{"hot_topics":[{"topic":"x","anime":"One Piece","heat":9}],"show_ranking":[{"anime":"One Piece","score":7,"sources_agreeing":3}]}');
    }

    static ShowrunnerReelDecision.Request decision(String id, String d) {
        ShowrunnerReelDecision.Request q = new ShowrunnerReelDecision.Request();
        q.reelId = id; q.decision = d; q.reason = 'test';
        return q;
    }

    @IsTest
    static void readActions() {
        ShowrunnerRecentReels.Request rq = new ShowrunnerRecentReels.Request();
        rq.count = 5;
        Assert.isTrue(ShowrunnerRecentReels.run(new List<ShowrunnerRecentReels.Request>{ rq })[0].reelsJson.contains('SR-1'));
        ShowrunnerReelReport.Request q = new ShowrunnerReelReport.Request();
        q.reelId = 'SR-1';
        String report = ShowrunnerReelReport.run(new List<ShowrunnerReelReport.Request>{ q })[0].reportJson;
        Assert.isTrue(report.contains('remakes_left') && report.contains('edit_score'));
        Assert.isTrue(ShowrunnerTrends.run(new List<String>{ '' })[0].trendsJson.contains('One Piece'));
        Assert.areEqual('[]', ShowrunnerLessons.run(new List<String>{ '' })[0].lessonsJson);
    }

    @IsTest
    static void approveRespectsQualityBar() {
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        Test.startTest();
        Assert.isTrue(ShowrunnerReelDecision.run(new List<ShowrunnerReelDecision.Request>{ decision('SR-2', 'approve') })[0].outcome.startsWith('Refused'));
        Assert.isTrue(ShowrunnerReelDecision.run(new List<ShowrunnerReelDecision.Request>{ decision('SR-1', 'approve') })[0].outcome.startsWith('Approved'));
        Assert.isTrue(ShowrunnerReelDecision.run(new List<ShowrunnerReelDecision.Request>{ decision('SR-2', 'hold') })[0].outcome.startsWith('Held'));
        Test.stopTest();
        Assert.isTrue([SELECT Hold_Reason__c FROM Anime_Content__c WHERE External_Id__c = 'SR-2'].Hold_Reason__c.startsWith('Showrunner:'));
        Assert.areEqual(1, [SELECT COUNT() FROM Agent_Activity__c WHERE Needs_Owner__c = true]);
    }

    @IsTest
    static void remakeHasALimit() {
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        ShowrunnerRemakeReel.Request q = new ShowrunnerRemakeReel.Request();
        q.reelId = 'SR-1'; q.notes = 'different show'; q.show = 'One Piece'; q.format = 'Backstory'; q.topic = 'Shanks';
        Test.startTest();
        Assert.isTrue(ShowrunnerRemakeReel.run(new List<ShowrunnerRemakeReel.Request>{ q })[0].outcome.startsWith('Remake 1'));
        Test.stopTest();
        Anime_Content__c c = [SELECT Format__c, Remakes__c, Showrunner_Directive__c, Status__c FROM Anime_Content__c WHERE External_Id__c = 'SR-1'];
        Assert.areEqual('Backstory', c.Format__c);
        Assert.isTrue(c.Showrunner_Directive__c.contains('Shanks'));
        update new Anime_Content__c(Id = [SELECT Id FROM Anime_Content__c WHERE External_Id__c = 'SR-1'].Id, Remakes__c = 2, Status__c = 'Rendered');
        Assert.isTrue(ShowrunnerRemakeReel.run(new List<ShowrunnerRemakeReel.Request>{ q })[0].outcome.startsWith('Refused'));
    }

    @IsTest
    static void lessonsAndOps() {
        ShowrunnerRecordLesson.Request l = new ShowrunnerRecordLesson.Request();
        l.lesson = 'Never post the same show twice in a row'; l.area = 'planning'; l.reelId = 'SR-1';
        Assert.isTrue(ShowrunnerRecordLesson.run(new List<ShowrunnerRecordLesson.Request>{ l })[0].outcome.startsWith('Recorded'));
        Assert.isTrue(ShowrunnerRecordLesson.run(new List<ShowrunnerRecordLesson.Request>{ l })[0].outcome.startsWith('Already'));
        ShowrunnerOps.Request n = new ShowrunnerOps.Request();
        n.action = 'notify'; n.message = 'look'; n.severity = 'critical';
        Assert.areEqual('Owner notified.', ShowrunnerOps.run(new List<ShowrunnerOps.Request>{ n })[0].outcome);
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        ShowrunnerOps.Request rt = new ShowrunnerOps.Request();
        rt.action = 'retry'; rt.reelId = 'SR-2'; rt.message = 'outage';
        Test.startTest();
        Assert.isTrue(ShowrunnerOps.run(new List<ShowrunnerOps.Request>{ rt })[0].outcome.startsWith('Retrying'));
        Test.stopTest();
    }

    @IsTest
    static void eventsWakeTheShowrunner() {
        Anime_Content__c c = [SELECT Id FROM Anime_Content__c WHERE External_Id__c = 'SR-2'];
        Test.startTest();
        update new Anime_Content__c(Id = c.Id, Hold_Reason__c = 'QA issues: edit quality 5/10');
        Test.stopTest();
        Agent_Activity__c a = [SELECT Action__c, Reel__c, Details__c FROM Agent_Activity__c WHERE Action__c = 'Handled event' LIMIT 1];
        Assert.areEqual('SR-2', a.Reel__c);
        Assert.isTrue(a.Details__c.contains('reel_held'));
    }

    @IsTest
    static void failuresWakeOpsAndOwnHoldsDoNot() {
        Anime_Content__c c = [SELECT Id FROM Anime_Content__c WHERE External_Id__c = 'SR-1'];
        Test.startTest();
        update new Anime_Content__c(Id = c.Id, Status__c = 'Failed', Error_Message__c = 'API access blocked');
        Test.stopTest();
        Assert.isTrue([SELECT Details__c FROM Agent_Activity__c WHERE Action__c = 'Handled event' LIMIT 1].Details__c.contains('reel_failed'));
        Assert.areEqual('plain', ShowrunnerEvents.answerText('plain'));
        Assert.areEqual('hi', ShowrunnerEvents.answerText('{"type":"Text","value":"hi"}'));
    }

    @IsTest
    static void showrunnerHoldsDoNotLoop() {
        Anime_Content__c c = [SELECT Id FROM Anime_Content__c WHERE External_Id__c = 'SR-2'];
        Test.startTest();
        update new Anime_Content__c(Id = c.Id, Hold_Reason__c = 'Showrunner: waiting for owner');
        Test.stopTest();
        Assert.areEqual(0, [SELECT COUNT() FROM Agent_Activity__c WHERE Action__c = 'Handled event']);
    }
}
