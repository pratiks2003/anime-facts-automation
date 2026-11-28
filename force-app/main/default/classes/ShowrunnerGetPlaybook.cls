/** Showrunner action: the agents' own playbook (current values, what each does) and recent strategy changes. */
public with sharing class ShowrunnerGetPlaybook {
    public class Request {
        @InvocableVariable(label='Key' description='Optional: one setting, or empty for all')
        public String key;
    }
    public class Result {
        @InvocableVariable(label='Playbook' description='Playbook as JSON')
        public String playbookJson;
    }

    @InvocableMethod(label='Get Playbook' description='The team playbook: explore share, drop rule, posting times, style defaults, focus/avoid shows, format mix, hook rules - plus recent changes and their results.')
    public static List<Result> run(List<Request> requests) {
        List<Map<String, Object>> changes = new List<Map<String, Object>>();
        for (Strategy_Change__c c : [SELECT CreatedDate, Setting__c, Old_Value__c, New_Value__c, Reason__c, Status__c, Baseline_Index__c,
                                     Result_Index__c, Reels_Since__c, Changed_By__c FROM Strategy_Change__c ORDER BY CreatedDate DESC LIMIT 10]) {
            changes.add(new Map<String, Object>{ 'when' => c.CreatedDate, 'setting' => c.Setting__c, 'from' => c.Old_Value__c,
                'to' => c.New_Value__c, 'why' => c.Reason__c, 'status' => c.Status__c, 'baseline' => c.Baseline_Index__c,
                'result' => c.Result_Index__c, 'reels_since' => c.Reels_Since__c, 'by' => c.Changed_By__c });
        }
        Result r = new Result();
        r.playbookJson = JSON.serialize(new Map<String, Object>{ 'values' => Playbook.getAll(), 'what_each_does' => Playbook.explanations(),
            'recent_changes' => changes, 'guardrail' => 'changes roll back automatically if the next 4+ Reels score 20% below the baseline' });
        return new List<Result>{ r };
    }
}