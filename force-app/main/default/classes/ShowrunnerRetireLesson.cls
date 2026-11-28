/** Showrunner action: retire a lesson the scoreboard no longer supports. */
public with sharing class ShowrunnerRetireLesson {
    public class Request {
        @InvocableVariable(label='Lesson' description='Exact text (or a unique part) of the lesson to retire' required=true)
        public String lesson;
        @InvocableVariable(label='Reason' description='The evidence that it no longer holds' required=true)
        public String reason;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='Retired or not found')
        public String outcome;
    }

    @InvocableMethod(label='Retire Lesson' description='Deactivates a lesson that the evidence no longer supports.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        String needle = String.valueOf(q.lesson).trim().left(120).toLowerCase();
        List<Agent_Lesson__c> found = new List<Agent_Lesson__c>();
        for (Agent_Lesson__c l : [SELECT Id, Lesson__c FROM Agent_Lesson__c WHERE Active__c = true LIMIT 500]) {
            if (String.isNotBlank(needle) && l.Lesson__c != null && l.Lesson__c.toLowerCase().contains(needle)) found.add(l);
        }
        if (found.size() != 1) {
            r.outcome = found.isEmpty() ? 'Not found among active lessons.' : 'Ambiguous: give more of the lesson text.';
            return new List<Result>{ r };
        }
        found[0].Active__c = false;
        update found;
        ShowrunnerSupport.log('Strategist', 'Retired lesson', null, found[0].Lesson__c + '\nWhy: ' + q.reason, false, 'info');
        r.outcome = 'Retired: ' + found[0].Lesson__c;
        return new List<Result>{ r };
    }
}