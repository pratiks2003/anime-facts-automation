@IsTest
private class LearningLoopTest {
    static Anime_Content__c reel(String id, Anime__c show, Datetime published, Decimal views, Decimal watchMs, String directive) {
        Anime_Content__c c = new Anime_Content__c(Name = id, External_Id__c = id, Format__c = 'Fact', Status__c = 'Published',
            Slot__c = 'Evening', Anime__c = show.Id, Showrunner_Directive__c = directive);
        insert c;
        insert new Social_Post__c(Content__c = c.Id, Status__c = 'Published', Published_At__c = published, Views__c = views,
            Avg_Watch_Time_Ms__c = watchMs, Instagram_Media_Id__c = 'm' + id, Metrics_Updated_At__c = Datetime.now().addHours(-2));
        return c;
    }

    @TestSetup
    static void data() {
        InstaAgentTestUtil.settings(true, true);
        Anime__c op = new Anime__c(Name = 'One Piece');
        Anime__c fn = new Anime__c(Name = 'Fool Night');
        insert new List<Anime__c>{ op, fn };
        Datetime now = Datetime.now();
        String planned = '{"by":"Showrunner Planner","style":{"transition":"flash","image_interval":1.6},"edits":{"hook":[{"fx":"impact_text"}],"twist":["freeze"]}}';
        reel('LL-1', op, now.addHours(-60), 2000, 9000, planned);
        reel('LL-2', op, now.addHours(-50), 1800, 8000, planned);
        reel('LL-3', fn, now.addHours(-40), 400, 3000, null);
        reel('LL-4', fn, now.addHours(-30), 300, 2500, null);
        reel('LL-5', op, now.addHours(-20), 1000, 6000, null);
        reel('LL-6', fn, now.addHours(-2), 50, 1000, null);
    }

    @IsTest
    static void scoresVerdictsAndEvidence() {
        Datetime now = Datetime.now();
        Assert.isTrue(Scoreboard.refresh(now) >= 6);
        Map<String, String> verdicts = new Map<String, String>();
        for (Anime_Content__c c : [SELECT External_Id__c, Verdict__c FROM Anime_Content__c]) verdicts.put(c.External_Id__c, c.Verdict__c);
        Assert.areEqual('Winner', verdicts.get('LL-1'));
        Assert.areEqual('Loser', verdicts.get('LL-4'));
        Assert.areEqual('Too young', verdicts.get('LL-6'));
        Map<String, Object> ins = Scoreboard.insights(now, null);
        String body = JSON.serialize(ins);
        Assert.isTrue(body.contains('DROPPED') && body.contains('impact_text') && body.contains('pace=fast cuts'), body);
        Assert.isTrue(ShowrunnerScoreboard.run(new List<ShowrunnerScoreboard.Request>{ new ShowrunnerScoreboard.Request() })[0].scoreboardJson.contains('by_show'));
        Assert.areEqual(1.5, Scoreboard.median(new List<Decimal>{ 1, 2 }));
    }

    @IsTest
    static void playbookGuardrailsAndRollback() {
        Scoreboard.refresh(Datetime.now());
        Assert.isTrue(Playbook.change('explore_share', '0.9', 'x', 'Strategist').startsWith('Refused'));
        Assert.isTrue(Playbook.change('nope', '1', 'x', 'Strategist').startsWith('Refused'));
        Assert.isTrue(Playbook.change('trending_audio_share', '1.5', 'x', 'Strategist').startsWith('Refused'));
        Assert.isTrue(Playbook.change('trending_audio_share', '0.5', 'compare trending vs library evenly', 'Strategist').startsWith('Changed'));
        insert new Agent_Playbook__c(Name = 'audience_online_hours', Key__c = 'audience_online_hours', Value__c = '{"followers_online_by_hour":{"21":120}}');
        Assert.isTrue(JSON.serialize(Playbook.data()).contains('followers_online_by_hour'));
        Assert.isTrue(Playbook.change('slot_times', '{"Morning":"09:00","Midday":"10:00"}', 'x', 'Strategist').startsWith('Refused'));
        Assert.isTrue(Playbook.change('format_mix', '{"Not A Format":0.5}', 'x', 'Strategist').startsWith('Refused'));
        Assert.isTrue(Playbook.change('explore_share', '0.3', '', 'Strategist').startsWith('Refused'));
        Assert.isTrue(Playbook.change('slot_times', '{"Morning":"08:30","Midday":"13:30","Evening":"19:30"}', 'evening audience peaks later', 'Strategist').startsWith('Changed'));
        Assert.areEqual('19:30', InstaAgentConfig.get().Evening_Slot_IST__c);
        Assert.isTrue(Playbook.change('hook_rules', '["Open on a question"]', 'winners open with questions', 'Strategist').startsWith('Changed'));
        Assert.isTrue(Playbook.change('hook_rules', '["Open on a question"]', 'same', 'Strategist').startsWith('No change'));

        // a change followed by 4 bad Reels is rolled back
        Strategy_Change__c ch = [SELECT Id FROM Strategy_Change__c WHERE Setting__c = 'slot_times'];
        Test.setCreatedDate(ch.Id, Datetime.now().addDays(-5));
        update new Strategy_Change__c(Id = ch.Id, Baseline_Index__c = 2.0);
        Assert.areEqual(1, Playbook.review());
        Assert.areEqual('Rolled back', [SELECT Status__c FROM Strategy_Change__c WHERE Id = :ch.Id].Status__c);
        Assert.areEqual('19:00', InstaAgentConfig.get().Evening_Slot_IST__c);

        ShowrunnerUpdatePlaybook.Request u = new ShowrunnerUpdatePlaybook.Request();
        u.key = 'drop_hours'; u.valueJson = '24'; u.reason = 'losers recover after a day';
        Assert.isTrue(ShowrunnerUpdatePlaybook.run(new List<ShowrunnerUpdatePlaybook.Request>{ u })[0].outcome.startsWith('Changed'));
        Assert.areEqual(24, Playbook.intValue('drop_hours', 48));
        String pb = ShowrunnerGetPlaybook.run(new List<ShowrunnerGetPlaybook.Request>{ new ShowrunnerGetPlaybook.Request() })[0].playbookJson;
        Assert.isTrue(pb.contains('drop_hours') && pb.contains('Rolled back'), pb);
        Assert.isNull(Playbook.validate('style_defaults', new Map<String, Object>{ 'transition' => 'flash' }));
        Assert.isNotNull(Playbook.validate('style_defaults', new Map<String, Object>{ 'neon' => true }));
    }

    @IsTest
    static void healthRequestsAndLessons() {
        String h = ShowrunnerSystemHealth.run(new List<ShowrunnerSystemHealth.Request>{ new ShowrunnerSystemHealth.Request() })[0].healthJson;
        Assert.isTrue(h.contains('metrics_hours_old'), h);
        ShowrunnerRequestImprovement.Request q = new ShowrunnerRequestImprovement.Request();
        q.title = 'Views arrive late'; q.details = 'metrics are 20 h old at planning time';
        Assert.isTrue(ShowrunnerRequestImprovement.run(new List<ShowrunnerRequestImprovement.Request>{ q })[0].outcome.startsWith('Filed'));
        Assert.isTrue(ShowrunnerRequestImprovement.run(new List<ShowrunnerRequestImprovement.Request>{ q })[0].outcome.startsWith('Already'));
        insert new Agent_Lesson__c(Lesson__c = 'Never post the same show twice', Area__c = 'planning', Active__c = true);
        ShowrunnerRetireLesson.Request l = new ShowrunnerRetireLesson.Request();
        l.lesson = 'same show twice'; l.reason = 'repeats of hot shows win now';
        Assert.isTrue(ShowrunnerRetireLesson.run(new List<ShowrunnerRetireLesson.Request>{ l })[0].outcome.startsWith('Retired'));
        Assert.isTrue(ShowrunnerRetireLesson.run(new List<ShowrunnerRetireLesson.Request>{ l })[0].outcome.startsWith('Not found'));
        ShowrunnerRecentReels.Request rq = new ShowrunnerRecentReels.Request();
        rq.count = 3;
        Assert.isTrue(ShowrunnerRecentReels.run(new List<ShowrunnerRecentReels.Request>{ rq })[0].reelsJson.contains('post_age_hours'));
    }

    @IsTest
    static void dailyReviewRunsOncePerDay() {
        Test.startTest();
        ShowrunnerEvents.dailyReview(Datetime.now());
        Test.stopTest();
        Assert.areEqual(1, [SELECT COUNT() FROM Agent_Activity__c WHERE Action__c = 'Daily review' AND Agent__c = 'Strategist']);
        ShowrunnerEvents.dailyReview(Datetime.now());  // already done today: nothing queued
        Assert.areEqual(1, [SELECT COUNT() FROM Agent_Activity__c WHERE Action__c = 'Daily review']);
    }

    @IsTest
    static void undecidedReelsAreNudgedThenPosted() {
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        Anime_Content__c c = new Anime_Content__c(Name = 'U', External_Id__c = 'LL-U', Format__c = 'Fact', Status__c = 'Rendered',
            Approval_Status__c = 'Pending', Hold_Reason__c = 'QA issues: edit quality 6/10', Slot__c = 'Evening',
            Scheduled_For__c = Datetime.now().addHours(1));
        insert c;
        Test.setCreatedDate(c.Id, Datetime.now().addHours(-2));
        Datetime later = Datetime.now().addHours(2);  // LastModifiedDate is "now", so judge from 2 h later
        ReelScheduler.nudgeUndecidedReels(later);
        Assert.areEqual(1, [SELECT COUNT() FROM Agent_Activity__c WHERE Reel__c = 'LL-U' AND Action__c = 'Nudge']);
        Test.startTest();
        ReelScheduler.nudgeUndecidedReels(later);
        Test.stopTest();
        Assert.areEqual('Approved', [SELECT Approval_Status__c FROM Anime_Content__c WHERE Id = :c.Id].Approval_Status__c);
    }
}
