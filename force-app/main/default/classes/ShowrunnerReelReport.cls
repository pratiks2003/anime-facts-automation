/** Showrunner action: everything known about one Reel - script, gate decision, quality checker, fact check, errors. */
public with sharing class ShowrunnerReelReport {
    public class Request {
        @InvocableVariable(label='Reel ID' description='External id of the Reel, e.g. R-20261128-AM' required=true)
        public String reelId;
    }
    public class Result {
        @InvocableVariable(label='Report' description='JSON: status, hold reason, scores, QA issues, quality checker findings, fact check, script scenes, error')
        public String reportJson;
    }

    @InvocableMethod(label='Get Reel Report' description='Full report for one Reel: script, scores, quality-checker and fact-check findings, hold reason and errors.')
    public static List<Result> run(List<Request> requests) {
        Anime_Content__c c = ShowrunnerSupport.reel(requests[0].reelId);
        List<Object> scenes = new List<Object>();
        Object script = ShowrunnerSupport.parse(c.Script_JSON__c);
        if (script instanceof Map<String, Object> && ((Map<String, Object>) script).get('scenes') instanceof List<Object>) {
            for (Object o : (List<Object>) ((Map<String, Object>) script).get('scenes')) {
                Map<String, Object> s = (Map<String, Object>) o;
                scenes.add(new Map<String, Object>{ 'role' => s.get('role'), 'narration' => s.get('narration') });
            }
        }
        List<Video_Job__c> jobs = [SELECT Job_Type__c, Status__c, Stage__c, Error_Message__c, Run_URL__c FROM Video_Job__c
                                   WHERE Content__c = :c.Id ORDER BY CreatedDate DESC LIMIT 1];
        Map<String, Object> report = new Map<String, Object>{
            'id' => c.External_Id__c, 'slot' => c.Slot__c, 'show' => c.Anime__r == null ? null : c.Anime__r.Name,
            'format' => c.Format__c, 'topic' => c.Topic__c, 'style' => c.Style__r == null ? null : c.Style__r.Name,
            'status' => c.Status__c, 'approval' => c.Approval_Status__c, 'hold_reason' => c.Hold_Reason__c,
            'director_score' => c.Quality_Score__c, 'qa_issues' => c.QA_Issues__c, 'director_notes' => c.Director_Notes__c,
            'render_report' => ShowrunnerSupport.parse(c.Render_Report__c), 'strategy' => ShowrunnerSupport.parse(c.Strategy__c),
            'scenes' => scenes, 'error' => c.Error_Message__c, 'remakes_so_far' => c.Remakes__c == null ? 0 : c.Remakes__c,
            'remakes_left' => ShowrunnerSupport.MAX_REMAKES - (c.Remakes__c == null ? 0 : c.Remakes__c.intValue()),
            'last_job' => jobs.isEmpty() ? null : new Map<String, Object>{ 'type' => jobs[0].Job_Type__c, 'status' => jobs[0].Status__c,
                'stage' => jobs[0].Stage__c, 'error' => jobs[0].Error_Message__c }
        };
        Result r = new Result();
        r.reportJson = JSON.serialize(report).abbreviate(60000);
        return new List<Result>{ r };
    }
}