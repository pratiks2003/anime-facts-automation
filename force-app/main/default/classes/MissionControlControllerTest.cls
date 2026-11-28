@IsTest
private class MissionControlControllerTest {
    @TestSetup
    static void data() {
        InstaAgentTestUtil.settings(true, true);
        Anime_Content__c live = new Anime_Content__c(Name = 'Live', External_Id__c = 'MC-1', Format__c = 'Fact', Status__c = 'Published',
            Approval_Status__c = 'Approved', Performance_Index__c = 1.4, Verdict__c = 'Winner', Topic__c = 'Gear 5',
            Showrunner_Directive__c = '{"by":"Showrunner Planner","show":"One Piece","notes":"hook hard","edits":{"hook":[{"fx":"impact_text"}],"twist":["freeze"]}}',
            Render_Report__c = '{"visual_qa":{"edit_score":8,"direction_score":7,"direction_notes":["ok"]},"effects_used":["impact_text","versus"]}');
        Anime_Content__c active = new Anime_Content__c(Name = 'Active', External_Id__c = 'MC-2', Format__c = 'Fact', Status__c = 'Generating',
            Approval_Status__c = 'Pending', Showrunner_Directive__c = '{"show":"Fool Night"}');
        insert new List<Anime_Content__c>{ live, active };
        insert new Video_Job__c(Content__c = active.Id, Status__c = 'Running', Stage__c = 'Rendering', Correlation_Id__c = 'MC-2-GEN-1');
        insert new Social_Post__c(Content__c = live.Id, Status__c = 'Published', Platform__c = 'Instagram', Instagram_Media_Id__c = 'm1',
            Views__c = 1200, Avg_Watch_Time_Ms__c = 9000, Published_At__c = Datetime.now().addHours(-20), Metrics_Updated_At__c = Datetime.now());
        insert new List<Agent_Activity__c>{
            new Agent_Activity__c(Name = 'a', Agent__c = 'Ops', Action__c = 'Health check', Needs_Owner__c = true, Severity__c = 'critical', Details__c = 'Token expired'),
            new Agent_Activity__c(Name = 'b', Agent__c = 'Editor', Action__c = 'Feature request', Details__c = 'hologram'),
            new Agent_Activity__c(Name = 'c', Agent__c = 'Planner', Action__c = 'Submit Plan', Reel__c = 'MC-2', Details__c = '{}')
        };
        insert new Agent_Playbook__c(Name = 'Explore share', Key__c = 'explore_share', Value__c = '0.2', Explanation__c = 'share of slots for experiments');
        insert new Strategy_Change__c(Name = 'x', Setting__c = 'explore_share', Old_Value__c = '0.2', New_Value__c = '0.3', Status__c = 'Active',
            Reason__c = 'test', Baseline_Index__c = 1, Changed_By__c = 'Strategist');
        insert new Trend_Brief__c(Name = 'b', Brief_Date__c = Date.today(), Summary__c = 's',
            Brief_JSON__c = '{"hot_topics":[{"topic":"Gear 5","anime":"One Piece","heat":9},{"topic":"Ivy","anime":"Fool Night","heat":8}]}');
    }

    @IsTest
    static void overviewHasEverySection() {
        Test.startTest();
        Map<String, Object> o = MissionControlController.getOverview();
        Test.stopTest();
        for (String key : new List<String>{ 'autopilot', 'inbox', 'reels', 'kpis', 'feed', 'playbook', 'changes', 'requests', 'trends', 'health' }) {
            Assert.isTrue(o.containsKey(key), key);
        }
        Map<String, Object> a = (Map<String, Object>) o.get('autopilot');
        Assert.isNotNull(a.get('nextSlotAt'));
        Map<String, Object> active = (Map<String, Object>) a.get('active');
        Assert.areEqual('MC-2', active.get('id'));
        Assert.areEqual(1, active.get('step'));
        Assert.areEqual('Fool Night', active.get('show'));
        Assert.areEqual(1, ((List<Object>) o.get('inbox')).size());
        List<Object> reels = (List<Object>) o.get('reels');
        Map<String, Object> live;
        for (Object r : reels) {
            if (((Map<String, Object>) r).get('id') == 'MC-1') live = (Map<String, Object>) r;
        }
        Assert.areEqual('Winner', live.get('verdict'));
        Assert.areEqual('Showrunner Planner', live.get('plannedBy'));
        Assert.isTrue(((List<String>) live.get('effects')).contains('freeze'));
        Assert.isTrue(((List<String>) live.get('effects')).contains('versus'));
        Assert.areEqual(9.0, live.get('watchSeconds'));
        Map<String, Object> k = (Map<String, Object>) o.get('kpis');
        Assert.areEqual(1200, (Decimal) k.get('views7d'));
        Assert.areEqual(1, k.get('winners'));
        Assert.areEqual(2, ((List<Object>) o.get('trends')).size());
        Assert.areEqual(1, ((List<Object>) o.get('requests')).size());
        Assert.areEqual(1, ((List<Object>) o.get('playbook')).size());
        Assert.areEqual(1, ((List<Object>) o.get('changes')).size());
        Assert.areEqual(1, ((Map<String, Object>) o.get('health')).get('openItems'));
    }

    @IsTest
    static void resolveAndAsk() {
        Agent_Activity__c a = [SELECT Id FROM Agent_Activity__c WHERE Needs_Owner__c = true LIMIT 1];
        MissionControlController.resolveItem(a.Id);
        Assert.isTrue([SELECT Resolved__c FROM Agent_Activity__c WHERE Id = :a.Id].Resolved__c);
        Assert.areEqual('', MissionControlController.askShowrunner(' '));
        Assert.areEqual('test', MissionControlController.askShowrunner('How are we doing?'));
    }

    @IsTest
    static void pipelineSteps() {
        Assert.areEqual(4, MissionControlController.step('Published', null));
        Assert.areEqual(3, MissionControlController.step('Publishing', null));
        Assert.areEqual(3, MissionControlController.step('Rendered', 'Waiting: post spacing'));
        Assert.areEqual(2, MissionControlController.step('Rendered', null));
        Assert.areEqual(1, MissionControlController.step('Generating', 'Rendering'));
        Assert.areEqual(0, MissionControlController.step('Queued', null));
    }
}
