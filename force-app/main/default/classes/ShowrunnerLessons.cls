/** Showrunner actions: read the agents' memory of lessons, and record a new lesson. */
public with sharing class ShowrunnerLessons {
    public class Request {
        @InvocableVariable(label='Area' description='Optional: planning, writing, visuals, music or ops (empty = all)')
        public String area;
    }

    public class Result {
        @InvocableVariable(label='Lessons' description='JSON list of active lessons: area, lesson, source Reel')
        public String lessonsJson;
    }

    @InvocableMethod(label='Get Lessons' description='Active lessons the team has learned (planning, writing, visuals, music, ops).')
    public static List<Result> run(List<Request> requests) {
        String area = requests.isEmpty() || String.isBlank(requests[0].area) ? null : requests[0].area;
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        List<Agent_Lesson__c> lessons = area == null
            ? [SELECT Area__c, Lesson__c, Source_Reel__c FROM Agent_Lesson__c WHERE Active__c = true ORDER BY CreatedDate DESC LIMIT 30]
            : [SELECT Area__c, Lesson__c, Source_Reel__c FROM Agent_Lesson__c WHERE Active__c = true AND Area__c = :area
               ORDER BY CreatedDate DESC LIMIT 30];
        for (Agent_Lesson__c l : lessons) {
            rows.add(new Map<String, Object>{ 'area' => l.Area__c, 'lesson' => l.Lesson__c, 'source' => l.Source_Reel__c });
        }
        Result r = new Result();
        r.lessonsJson = JSON.serialize(rows);
        return new List<Result>{ r };
    }
}