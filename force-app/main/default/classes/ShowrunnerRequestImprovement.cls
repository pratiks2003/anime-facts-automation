/** Showrunner action: report a gap in the system itself (stale data, missing tool, editor limit) to the developers. */
public with sharing class ShowrunnerRequestImprovement {
    public class Request {
        @InvocableVariable(label='Title' description='Short name of the problem or missing capability' required=true)
        public String title;
        @InvocableVariable(label='Details' description='What is wrong or missing, the evidence, and what it costs in views' required=true)
        public String details;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='Filed or already open')
        public String outcome;
    }

    @InvocableMethod(label='Request Improvement' description='Files an improvement request for the developers when the system (not one Reel) has a gap.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        String title = q.title == null ? 'Improvement' : q.title.trim().abbreviate(80);
        if ([SELECT COUNT() FROM Agent_Activity__c WHERE Action__c = 'Improvement request' AND Name = :title AND Resolved__c = false] > 0) {
            r.outcome = 'Already open: ' + title;
            return new List<Result>{ r };
        }
        insert new Agent_Activity__c(Name = title, Agent__c = 'Strategist', Action__c = 'Improvement request', Severity__c = 'info',
            Needs_Owner__c = false, Details__c = (title + '\n' + q.details).abbreviate(5000));
        r.outcome = 'Filed: ' + title;
        return new List<Result>{ r };
    }
}