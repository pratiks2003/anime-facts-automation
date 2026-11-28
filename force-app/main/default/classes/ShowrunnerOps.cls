/** Showrunner Ops actions: retry a failed job, or notify the owner. */
public with sharing class ShowrunnerOps {
    public class Request {
        @InvocableVariable(label='Action' description='retry (re-run the failed job) or notify (message the owner)' required=true)
        public String action;
        @InvocableVariable(label='Reel ID' description='External id of the Reel (optional for notify)')
        public String reelId;
        @InvocableVariable(label='Message' description='Reason for the retry, or the message for the owner' required=true)
        public String message;
        @InvocableVariable(label='Severity' description='info, warning or critical (notify only)')
        public String severity;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='What happened')
        public String outcome;
    }

    @InvocableMethod(label='Ops Action' description='Retry a failed generate/publish job (max 3 attempts), or send the owner a note on the admin site.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        if (q.action == 'retry') {
            Anime_Content__c c = ShowrunnerSupport.reel(q.reelId);
            List<Video_Job__c> jobs = [SELECT Job_Type__c FROM Video_Job__c WHERE Content__c = :c.Id ORDER BY CreatedDate DESC LIMIT 1];
            Integer attempts = c.Attempts__c == null ? 0 : c.Attempts__c.intValue();
            if (attempts >= 3) {
                r.outcome = 'Refused: ' + q.reelId + ' already had ' + attempts + ' attempts. Notify the owner instead.';
                return new List<Result>{ r };
            }
            String type = jobs.isEmpty() ? ReelDispatchQueueable.GENERATE : jobs[0].Job_Type__c;
            if (type == ReelDispatchQueueable.PUBLISH) {
                c.Status__c = 'Rendered';
            } else {
                c.Status__c = 'Queued';
            }
            c.Error_Message__c = null;
            update c;
            System.enqueueJob(new ReelDispatchQueueable(new List<Id>{ c.Id }, type));
            ShowrunnerSupport.log('Ops', 'Retry ' + type, q.reelId, q.message, false, 'info');
            r.outcome = 'Retrying the ' + type + ' job for ' + q.reelId + '.';
        } else {
            String sev = new Set<String>{ 'info', 'warning', 'critical' }.contains(q.severity) ? q.severity : 'warning';
            ShowrunnerSupport.log('Showrunner', 'Note for owner', q.reelId, q.message, true, sev);
            r.outcome = 'Owner notified.';
        }
        return new List<Result>{ r };
    }
}