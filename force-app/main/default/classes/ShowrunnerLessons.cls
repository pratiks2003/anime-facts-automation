/** Showrunner actions: read the agents' memory of lessons, and record a new lesson. */
public with sharing class ShowrunnerLessons {
    public class Result {
        @InvocableVariable(label='Lessons' description='JSON list of active lessons: area, lesson, source Reel')
        public String lessonsJson;
    }

    @InvocableMethod(label='Get Lessons' description='Active lessons the team has learned (planning, writing, visuals, music, ops).')
    public static List<Result> run(List<String> unused) {
        List<Map<String, Object>> rows = new List<Map<String, Object>>();
        for (Agent_Lesson__c l : [SELECT Area__c, Lesson__c, Source_Reel__c, CreatedDate FROM Agent_Lesson__c
                                  WHERE Active__c = true ORDER BY CreatedDate DESC LIMIT 30]) {
            rows.add(new Map<String, Object>{ 'area' => l.Area__c, 'lesson' => l.Lesson__c, 'source' => l.Source_Reel__c });
        }
        Result r = new Result();
        r.lessonsJson = JSON.serialize(rows);
        return new List<Result>{ r };
    }
}