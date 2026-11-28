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
        Assert.isTrue(ShowrunnerTrends.run(new List<ShowrunnerTrends.Request>{ new ShowrunnerTrends.Request() })[0].trendsJson.contains('One Piece'));
        Assert.areEqual('[]', ShowrunnerLessons.run(new List<ShowrunnerLessons.Request>{ new ShowrunnerLessons.Request() })[0].lessonsJson);
    }

    @IsTest
    static void expertsDecideNoHolds() {
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        Test.startTest();
        Assert.isTrue(ShowrunnerReelDecision.run(new List<ShowrunnerReelDecision.Request>{ decision('SR-2', 'hold') })[0].outcome.startsWith('Refused'));
        String low = ShowrunnerReelDecision.run(new List<ShowrunnerReelDecision.Request>{ decision('SR-2', 'approve') })[0].outcome;
        Assert.isTrue(low.startsWith('Approved') && low.contains('5/10'), low);
        Assert.isTrue(ShowrunnerReelDecision.run(new List<ShowrunnerReelDecision.Request>{ decision('SR-1', 'approve') })[0].outcome.startsWith('Approved'));
        Test.stopTest();
        Assert.areEqual(0, [SELECT COUNT() FROM Agent_Activity__c WHERE Needs_Owner__c = true]);
    }

    @IsTest
    static void plannerSubmitsAPlan() {
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        insert new Anime_Content__c(Name = 'R3', External_Id__c = 'SR-3', Format__c = 'Quiz', Status__c = 'Queued', Approval_Status__c = 'Pending');
        ShowrunnerSubmitPlan.Request q = new ShowrunnerSubmitPlan.Request();
        q.reelId = 'SR-3'; q.show = 'One Piece'; q.topic = 'Why Gear 5 is everywhere today'; q.format = 'Backstory'; q.brief = 'hook on the laugh';
        q.style = '{"transition":"flash"}'; q.edits = '{"hook":["freeze"]}';
        Test.startTest();
        Assert.isTrue(ShowrunnerSubmitPlan.run(new List<ShowrunnerSubmitPlan.Request>{ q })[0].outcome.startsWith('Planned'));
        q.reelId = 'SR-1';
        Assert.isTrue(ShowrunnerSubmitPlan.run(new List<ShowrunnerSubmitPlan.Request>{ q })[0].outcome.startsWith('Refused'));
        Test.stopTest();
        Anime_Content__c c = [SELECT Format__c, Topic__c, Showrunner_Directive__c FROM Anime_Content__c WHERE External_Id__c = 'SR-3'];
        Assert.areEqual('Backstory', c.Format__c);
        Assert.isTrue(c.Showrunner_Directive__c.contains('Gear 5') && c.Showrunner_Directive__c.contains('Planner'));
        Assert.isTrue(c.Showrunner_Directive__c.contains('"edits":{"hook":["freeze"]}'), c.Showrunner_Directive__c);
    }

    @IsTest
    static void dueSlotsGoToThePlannerWithFallback() {
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        Anime_Content__c c = new Anime_Content__c(Name = 'R4', External_Id__c = 'SR-4', Format__c = 'Quiz', Status__c = 'Queued',
            Approval_Status__c = 'Pending', Slot__c = 'Morning', Scheduled_For__c = Datetime.now().addHours(2));
        insert c;
        Test.startTest();
        ShowrunnerEvents.planSlots(new List<Id>{ c.Id });
        Test.stopTest();
        String details = [SELECT Details__c FROM Agent_Activity__c WHERE Action__c = 'Handled event' LIMIT 1].Details__c;
        Assert.isTrue(details.contains('plan_due') && details.contains('default plan'), details);
    }

    @IsTest
    static void postingLimitWaitsReleaseThemselves() {
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        Anime_Content__c c = [SELECT Id FROM Anime_Content__c WHERE External_Id__c = 'SR-2'];
        update new Anime_Content__c(Id = c.Id, Hold_Reason__c = 'Posting limit: 6 Reels in 24 h. Posts automatically when the window opens.');
        Test.startTest();
        ReelScheduler.releasePostingLimitHolds(Datetime.now());
        Test.stopTest();
        Anime_Content__c after = [SELECT Approval_Status__c, Hold_Reason__c FROM Anime_Content__c WHERE Id = :c.Id];
        Assert.areEqual('Approved', after.Approval_Status__c);
        Assert.isNull(after.Hold_Reason__c);
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
        ShowrunnerLessons.Request lr = new ShowrunnerLessons.Request();
        lr.area = 'planning';
        Assert.isTrue(ShowrunnerLessons.run(new List<ShowrunnerLessons.Request>{ lr })[0].lessonsJson.contains('same show'));
        lr.area = 'visuals';
        Assert.areEqual('[]', ShowrunnerLessons.run(new List<ShowrunnerLessons.Request>{ lr })[0].lessonsJson);
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

    @IsTest
    static void editorCapabilitiesAreReadable() {
        ShowrunnerEditorCapabilities.Request q = new ShowrunnerEditorCapabilities.Request();
        q.topic = 'effects';
        String body = ShowrunnerEditorCapabilities.run(new List<ShowrunnerEditorCapabilities.Request>{ q })[0].capabilitiesJson;
        Assert.isTrue(body.contains('versus') && body.contains('how_to_request'), body);
        Map<String, Object> d = new Map<String, Object>();
        ShowrunnerEditorCapabilities.addEditing(d, 'not json', '{"all":["letterbox"]}');
        Assert.areEqual('not json', d.get('style'));
        Assert.isTrue(d.get('edits') instanceof Map<String, Object>);
    }

    @IsTest
    static void findFactsSearchesTheMirror() {
        insert new Anime_Knowledge__c(Name = 'Ivy the spiriflor', Knowledge_Id__c = 'FN-T1', Content__c = 'Ivy stays mobile after transfloration.');
        Test.setFixedSearchResults(new List<Id>{ [SELECT Id FROM Anime_Knowledge__c WHERE Knowledge_Id__c = 'FN-T1'].Id });
        ShowrunnerFindFacts.Request q = new ShowrunnerFindFacts.Request();
        q.searchText = 'Ivy "spiriflor"';
        String body = ShowrunnerFindFacts.run(new List<ShowrunnerFindFacts.Request>{ q })[0].factsJson;
        Assert.isTrue(body.contains('FN-T1'), body);
        q.searchText = 'x';
        Assert.areEqual('[]', ShowrunnerFindFacts.run(new List<ShowrunnerFindFacts.Request>{ q })[0].factsJson);
    }

    @IsTest
    static void plannerCannotPickADroppedShow() {
        Anime__c show = new Anime__c(Name = 'Loser Show');
        insert show;
        for (Integer i = 0; i < 3; i++) {
            insert new Anime_Content__c(Name = 'L' + i, External_Id__c = 'SR-L' + i, Format__c = 'Fact', Status__c = 'Published',
                Anime__c = show.Id, Verdict__c = 'Loser', Performance_Index__c = 0.4);
        }
        insert new Anime_Content__c(Name = 'R5', External_Id__c = 'SR-5', Format__c = 'Quiz', Status__c = 'Queued', Approval_Status__c = 'Pending');
        ShowrunnerSubmitPlan.Request q = new ShowrunnerSubmitPlan.Request();
        q.reelId = 'SR-5'; q.show = 'Loser Show'; q.topic = 't'; q.format = 'Fact'; q.brief = 'b';
        String outcome = ShowrunnerSubmitPlan.run(new List<ShowrunnerSubmitPlan.Request>{ q })[0].outcome;
        Assert.isTrue(outcome.startsWith('Refused') && outcome.contains('DROPPED'), outcome);
    }
}
