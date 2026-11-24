/**
 * Hourly heartbeat (scheduled at minute 40): plans due slots, dispatches them, and
 * times out jobs that never called back.
 *
 * Schedule once from Anonymous Apex:  ReelScheduler.scheduleHourly();
 */
public with sharing class ReelScheduler implements Schedulable {
    public static final String JOB_NAME = 'InstaAgent Reel Scheduler';
    public static final Integer JOB_TIMEOUT_MINUTES = 75;

    public void execute(SchedulableContext context) {
        tick(Datetime.now());
    }

    public static void tick(Datetime now) {
        timeOutStaleJobs(now);
        if (!InstaAgentConfig.get().Automation_Enabled__c) {
            return;
        }
        List<Anime_Content__c> planned = ReelSlotPlanner.planDue(now);
        if (!planned.isEmpty()) {
            List<Id> ids = new List<Id>(new Map<Id, Anime_Content__c>(planned).keySet());
            System.enqueueJob(new ReelDispatchQueueable(ids, ReelDispatchQueueable.GENERATE));
        }
    }

    public static void timeOutStaleJobs(Datetime now) {
        Datetime cutoff = now.addMinutes(-JOB_TIMEOUT_MINUTES);
        List<Video_Job__c> stale = [
            SELECT Id, Content__c
            FROM Video_Job__c
            WHERE Status__c IN ('Dispatched', 'Running') AND Dispatched_At__c < :cutoff
        ];
        if (stale.isEmpty()) {
            return;
        }
        Set<Id> contentIds = new Set<Id>();
        for (Video_Job__c j : stale) {
            j.Status__c = 'Timed Out';
            j.Completed_At__c = now;
            j.Error_Message__c = 'No callback from GitHub within ' + JOB_TIMEOUT_MINUTES + ' minutes';
            contentIds.add(j.Content__c);
        }
        update stale;
        List<Anime_Content__c> contents = [
            SELECT Id
            FROM Anime_Content__c
            WHERE Id IN :contentIds AND Status__c IN ('Queued', 'Generating', 'Publishing')
        ];
        for (Anime_Content__c c : contents) {
            c.Status__c = 'Failed';
            c.Error_Message__c = 'GitHub job timed out';
        }
        update contents;
    }

    public static Id scheduleHourly() {
        for (CronTrigger ct : [SELECT Id FROM CronTrigger WHERE CronJobDetail.Name = :JOB_NAME]) {
            System.abortJob(ct.Id);
        }
        return System.schedule(JOB_NAME, '0 40 * * * ?', new ReelScheduler());
    }
}
