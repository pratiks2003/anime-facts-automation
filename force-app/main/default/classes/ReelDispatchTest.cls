@IsTest
private class ReelDispatchTest {
    static Anime_Content__c reel(String status) {
        Anime_Content__c c = new Anime_Content__c(
            Name = 'Test Reel',
            External_Id__c = 'R-TEST-' + Crypto.getRandomInteger(),
            Format__c = 'Fact',
            Slot__c = 'Morning',
            Status__c = status,
            Approval_Status__c = 'Pending',
            Dry_Run__c = true
        );
        insert c;
        return c;
    }

    @IsTest
    static void generateDispatchSucceeds() {
        InstaAgentTestUtil.settings(true, false);
        Anime_Content__c c = reel('Queued');
        InstaAgentTestUtil.GitHubMock mock = new InstaAgentTestUtil.GitHubMock(204);
        Test.setMock(HttpCalloutMock.class, mock);
        Test.startTest();
        System.enqueueJob(new ReelDispatchQueueable(new List<Id>{ c.Id }, ReelDispatchQueueable.GENERATE));
        Test.stopTest();

        Anime_Content__c after = [SELECT Status__c, Attempts__c FROM Anime_Content__c WHERE Id = :c.Id];
        Assert.areEqual('Generating', after.Status__c);
        Assert.areEqual(1, after.Attempts__c);
        Video_Job__c job = [SELECT Status__c, Job_Type__c, Correlation_Id__c FROM Video_Job__c];
        Assert.areEqual('Dispatched', job.Status__c);
        Assert.areEqual('Generate', job.Job_Type__c);

        HttpRequest req = mock.requests[0];
        Assert.areEqual('callout:GitHub_API/repos/owner/repo/actions/workflows/reel.yml/dispatches', req.getEndpoint());
        Map<String, Object> body = (Map<String, Object>) JSON.deserializeUntyped(req.getBody());
        Map<String, Object> inputs = (Map<String, Object>) body.get('inputs');
        Assert.areEqual('main', body.get('ref'));
        Assert.areEqual(job.Correlation_Id__c, inputs.get('job_id'));
        Assert.areEqual('Fact', inputs.get('format'));
        Assert.areEqual('true', inputs.get('auto_approve'));
    }

    @IsTest
    static void dispatchFailureMarksReelFailed() {
        InstaAgentTestUtil.settings(true, false);
        Anime_Content__c c = reel('Queued');
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(401));
        Test.startTest();
        System.enqueueJob(new ReelDispatchQueueable(new List<Id>{ c.Id }, ReelDispatchQueueable.GENERATE));
        Test.stopTest();
        Anime_Content__c after = [SELECT Status__c, Error_Message__c FROM Anime_Content__c WHERE Id = :c.Id];
        Assert.areEqual('Failed', after.Status__c);
        Assert.isTrue(after.Error_Message__c.contains('401'));
        Assert.areEqual('Failed', [SELECT Status__c FROM Video_Job__c].Status__c);
    }

    @IsTest
    static void manualApprovalDispatchesPublish() {
        System.runAs(InstaAgentTestUtil.adminUser()) {
            InstaAgentTestUtil.settings(true, false);
            Anime_Content__c c = reel('Rendered');
            InstaAgentTestUtil.GitHubMock mock = new InstaAgentTestUtil.GitHubMock(204);
            Test.setMock(HttpCalloutMock.class, mock);
            Test.startTest();
            AdminPanelController.approveReel(c.Id);
            Test.stopTest();
            Assert.areEqual('Publishing', [SELECT Status__c FROM Anime_Content__c WHERE Id = :c.Id].Status__c);
            Assert.isTrue(mock.requests[0].getEndpoint().endsWith('/publish.yml/dispatches'));
            Assert.areEqual('Publish', [SELECT Job_Type__c FROM Video_Job__c].Job_Type__c);
        }
    }

    @IsTest
    static void schedulerTickPlansAndDispatches() {
        InstaAgentTestUtil.settings(true, true);
        Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
        Test.startTest();
        ReelScheduler.tick(InstaAgentTestUtil.morningSlotGmt().addMinutes(-20));
        Test.stopTest();
        Anime_Content__c c = [SELECT External_Id__c, Status__c FROM Anime_Content__c];
        Assert.areEqual('R-20261125-AM', c.External_Id__c);
        Assert.areEqual('Generating', c.Status__c);
    }

    @IsTest
    static void schedulerDoesNothingWhenDisabled() {
        InstaAgentTestUtil.settings(false, true);
        Test.startTest();
        ReelScheduler.tick(InstaAgentTestUtil.morningSlotGmt().addMinutes(-20));
        Test.stopTest();
        Assert.areEqual(0, [SELECT COUNT() FROM Anime_Content__c]);
    }

    @IsTest
    static void staleJobsTimeOut() {
        InstaAgentTestUtil.settings(false, false);
        Anime_Content__c c = reel('Generating');
        insert new Video_Job__c(
            Content__c = c.Id,
            Job_Type__c = 'Generate',
            Correlation_Id__c = 'J-1',
            Status__c = 'Dispatched',
            Dispatched_At__c = Datetime.now().addHours(-3)
        );
        ReelScheduler.timeOutStaleJobs(Datetime.now());
        Assert.areEqual('Timed Out', [SELECT Status__c FROM Video_Job__c].Status__c);
        Assert.areEqual('Failed', [SELECT Status__c FROM Anime_Content__c WHERE Id = :c.Id].Status__c);
    }

    @IsTest
    static void scheduleHourlyReplacesExistingJob() {
        Test.startTest();
        ReelScheduler.scheduleHourly();
        ReelScheduler.scheduleHourly();
        Test.stopTest();
        Assert.areEqual(1, [SELECT COUNT() FROM CronTrigger WHERE CronJobDetail.Name = :ReelScheduler.JOB_NAME]);
    }
}
