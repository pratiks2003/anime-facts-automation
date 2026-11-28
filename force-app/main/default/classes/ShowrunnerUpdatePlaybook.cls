/** Showrunner action: change one playbook setting (validated, logged, auto-rollback). */
public with sharing class ShowrunnerUpdatePlaybook {
    public class Request {
        @InvocableVariable(label='Key' description='Setting key from get_playbook' required=true)
        public String key;
        @InvocableVariable(label='Value' description='New value as JSON' required=true)
        public String valueJson;
        @InvocableVariable(label='Reason' description='The hypothesis and the scoreboard evidence behind it' required=true)
        public String reason;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='Changed or refused, and why')
        public String outcome;
    }

    @InvocableMethod(label='Update Playbook' description='Changes one playbook setting. Refused when out of range or without evidence. Rolled back automatically if results drop.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        r.outcome = Playbook.change(q.key == null ? null : q.key.trim(), q.valueJson, q.reason, 'Strategist');
        return new List<Result>{ r };
    }
}