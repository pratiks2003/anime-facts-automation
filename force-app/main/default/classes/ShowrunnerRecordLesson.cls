/** Showrunner action: remember a lesson so planning, writing and editing use it next time. */
public with sharing class ShowrunnerRecordLesson {
    public class Request {
        @InvocableVariable(label='Lesson' description='One concrete, reusable rule learned from a result or mistake' required=true)
        public String lesson;
        @InvocableVariable(label='Area' description='planning, writing, visuals, music or ops' required=true)
        public String area;
        @InvocableVariable(label='Reel ID' description='The Reel this was learned from (optional)')
        public String reelId;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='What was recorded')
        public String outcome;
    }

    @InvocableMethod(label='Record Lesson' description='Saves a lesson to the team memory; future planning, writing and editing read it.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        String area = new Set<String>{ 'planning', 'writing', 'visuals', 'music', 'ops' }.contains(q.area) ? q.area : 'planning';
        String text = q.lesson == null ? '' : q.lesson.trim();
        Result r = new Result();
        for (Agent_Lesson__c l : [SELECT Lesson__c FROM Agent_Lesson__c WHERE Active__c = true AND Area__c = :area LIMIT 200]) {
            if (l.Lesson__c != null && l.Lesson__c.equalsIgnoreCase(text)) {
                r.outcome = 'Already known: ' + text;
                return new List<Result>{ r };
            }
        }
        insert new Agent_Lesson__c(Name = text.abbreviate(80), Lesson__c = text.abbreviate(2000), Area__c = area, Active__c = true,
            Source_Reel__c = q.reelId, Recorded_By__c = 'Showrunner');
        ShowrunnerSupport.log('Showrunner', 'Record Lesson', q.reelId, area + ': ' + text, false, 'info');
        r.outcome = 'Recorded (' + area + '): ' + text;
        return new List<Result>{ r };
    }
}