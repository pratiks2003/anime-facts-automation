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
            // the Showrunner's Planner decides what each slot becomes (falls back to direct rendering)
            ShowrunnerEvents.planSlots(new List<Id>(new Map<Id, Anime_Content__c>(planned).keySet()));
        }
        releasePostingLimitHolds(now);
    }

    /** Reels that only waited for the posting limit are posted automatically once the window allows. */
    public static void releasePostingLimitHolds(Datetime now) {
        Map<String, Object> policy = InstaAgentConfig.postingPolicy();
        if ((Boolean) policy.get('enabled') && (Integer) policy.get('posts_last_24h') >= (Integer) policy.get('max_per_day')) {
            return;
        }
        Integer gap = (Integer) policy.get('min_gap_minutes');
        List<Social_Post__c> last = [SELECT Published_At__c FROM Social_Post__c WHERE Status__c = 'Published' AND Published_At__c != null
                                     ORDER BY Published_At__c DESC LIMIT 1];
        if ((Boolean) policy.get('enabled') && !last.isEmpty() && last[0].Published_At__c > now.addMinutes(-gap)) {
            return;
        }
        List<Anime_Content__c> waiting = [SELECT Id FROM Anime_Content__c WHERE Status__c = 'Rendered' AND Approval_Status__c = 'Pending'
                                          AND Hold_Reason__c LIKE 'Posting limit%' ORDER BY CreatedDate ASC LIMIT 1];
        if (!waiting.isEmpty()) {
            update new Anime_Content__c(Id = waiting[0].Id, Approval_Status__c = 'Approved', Hold_Reason__c = null);
            ShowrunnerSupport.log('Ops', 'Released posting-limit wait', null, 'Posting window open again', false, 'info');
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
