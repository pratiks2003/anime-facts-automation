@IsTest
private class AdminAppControllerTest {
    @TestSetup
    static void data() {
        InstaAgentTestUtil.settings(true, true);
        Anime_Content__c c = new Anime_Content__c(Name = 'Story', External_Id__c = 'AA-1', Format__c = 'Fact', Status__c = 'Generating',
            Approval_Status__c = 'Pending', Topic__c = 'Gear 5',
            Showrunner_Directive__c = '{"by":"Showrunner Planner","show":"One Piece","notes":"hook hard","edits":{"hook":[{"fx":"impact_text"}]},"style":{"transition":"flash"}}',
            Render_Report__c = '{"visual_qa":{"edit_score":8,"direction_score":9},"effects_used":["impact_text"]}');
        Anime_Content__c done = new Anime_Content__c(Name = 'Done', External_Id__c = 'AA-2', Format__c = 'Quiz', Status__c = 'Published',
            Approval_Status__c = 'Approved', Verdict__c = 'Winner', Performance_Index__c = 1.6);
        insert new List<Anime_Content__c>{ c, done };
        insert new Video_Job__c(Content__c = c.Id, Status__c = 'Running', Stage__c = 'Rendering', Correlation_Id__c = 'AA-1-GEN-1',
            Job_Type__c = 'Generate', Live_Log__c = '{"t":"2026-11-28T10:00:00Z","kind":"plan","title":"Planned"}\nnot json\n'
                + '{"t":"2026-11-28T10:05:00Z","kind":"qa","title":"Quality round 1","score":7.5,"score2":9}\n{"kind":"shots","title":"no time"}');
        insert new Social_Post__c(Content__c = done.Id, Status__c = 'Published', Platform__c = 'Instagram', Instagram_Media_Id__c = 'm2',
            Views__c = 900, Avg_Watch_Time_Ms__c = 8000, Published_At__c = Datetime.now().addHours(-30));
        insert new List<Agent_Activity__c>{
            new Agent_Activity__c(Name = 'p', Agent__c = 'Planner', Action__c = 'Submit Plan', Reel__c = 'AA-1', Details__c = 'Planned it'),
            new Agent_Activity__c(Name = 'Views arrive late', Agent__c = 'Strategist', Action__c = 'Improvement request', Details__c = 'stale'),
            new Agent_Activity__c(Name = 'r', Agent__c = 'Strategist', Action__c = 'Daily review', Details__c = 'EVENT x\n\nShowrunner: All good')
        };
        insert new Strategy_Change__c(Name = 'x', Setting__c = 'explore_share', Old_Value__c = '0.2', New_Value__c = '0.3', Status__c = 'Active',
            Reason__c = 'test', Baseline_Index__c = 1, Changed_By__c = 'Strategist');
        insert new Trend_Brief__c(Name = 'b', Brief_Date__c = Date.today(), Summary__c = 's',
            Brief_JSON__c = '{"hot_topics":[{"topic":"Gear 5","anime":"One Piece","heat":9}]}');
    }

    @IsTest
    static void storyMergesAgentsJobsAndLiveLog() {
        Map<String, Object> s = AdminAppController.getReelStory('AA-1');
        Assert.areEqual(true, s.get('found'));
        Assert.areEqual(true, s.get('inProgress'));
        List<Object> events = (List<Object>) s.get('events');
        Assert.isTrue(events.size() >= 4, String.valueOf(events.size())); // agent + job + 3 valid log lines
        Assert.isNotNull(s.get('plan'));
        Assert.isTrue((Integer) s.get('step') >= 2);
        Map<String, Object> def = AdminAppController.getReelStory(null);
        Assert.areEqual(true, def.get('found'));
        Assert.areEqual(false, AdminAppController.getReelStory('nope').get('found'));
        Assert.isTrue(AdminAppController.listStoryReels().size() >= 2);
        Assert.areEqual(6, AdminAppController.step('Published', 'Approved', null, new List<Map<String, Object>>()));
        Assert.areEqual(4, AdminAppController.step('Rendered', 'Pending', null, new List<Map<String, Object>>()));
        Assert.areEqual(1, AdminAppController.step('Generating', 'Pending', 'Writing script', new List<Map<String, Object>>()));
        Assert.areEqual(0, AdminAppController.step('Queued', 'Pending', null, new List<Map<String, Object>>()));
        Assert.areEqual(5, AdminAppController.step('Publishing', 'Approved', null, new List<Map<String, Object>>()));
    }

    @IsTest
    static void growthAndHealth() {
        Map<String, Object> g = AdminAppController.getGrowth();
        for (String k : new List<String>{ 'scoreboard', 'playbook', 'data', 'changes', 'requests', 'dailyReview', 'trends' }) {
            Assert.isTrue(g.containsKey(k), k);
        }
        Assert.areEqual(1, ((List<Object>) g.get('requests')).size());
        Assert.areEqual('All good', ((Map<String, Object>) g.get('dailyReview')).get('text'));
        Map<String, Object> h = AdminAppController.getHealth();
        Assert.isTrue(h.containsKey('report') && h.containsKey('posting'));
    }
}
