/**
 * Showrunner actions on a rendered Reel: approve it for publishing, or hold it.
 * Guardrail (code, not the agent): approval is refused when the quality checker scored the edit below 7
 * or the fact checker flagged a problem. Publishing still goes through the posting limit.
 */
public with sharing class ShowrunnerReelDecision {
    public class Request {
        @InvocableVariable(label='Reel ID' description='External id of the Reel' required=true)
        public String reelId;
        @InvocableVariable(label='Decision' description='approve or hold' required=true)
        public String decision;
        @InvocableVariable(label='Reason' description='Why (shown to the owner)' required=true)
        public String reason;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='What happened')
        public String outcome;
    }

    @InvocableMethod(label='Decide Reel' description='Approve a rendered Reel for publishing (only if it meets the quality bar) or hold it for the owner.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        Anime_Content__c c = ShowrunnerSupport.reel(q.reelId);
        if (q.decision == 'approve') {
            String blocker = qualityBlocker(c);
            if (blocker != null) {
                r.outcome = 'Refused: ' + blocker + ' Remake it or hold it.';
                return new List<Result>{ r };
            }
            if (c.Status__c != 'Rendered') {
                r.outcome = 'Refused: only rendered Reels can be approved (status ' + c.Status__c + ').';
                return new List<Result>{ r };
            }
            c.Approval_Status__c = 'Approved';
            c.Hold_Reason__c = null;
            update c; // trigger -> publish workflow (which enforces the posting limit)
            ShowrunnerSupport.log('Showrunner', 'Approve Reel', q.reelId, q.reason, false, 'info');
            r.outcome = 'Approved: ' + q.reelId + ' goes to the publish queue.';
        } else {
            c.Approval_Status__c = 'Pending';
            c.Hold_Reason__c = ('Showrunner: ' + q.reason).abbreviate(255);
            if (c.Status__c == 'Approved' || c.Status__c == 'Queued') c.Status__c = 'Rendered';
            update c;
            ShowrunnerSupport.log('Showrunner', 'Hold Reel', q.reelId, q.reason, true, 'warning');
            r.outcome = 'Held: ' + q.reelId + ' waits for the owner.';
        }
        return new List<Result>{ r };
    }

    @TestVisible
    private static String qualityBlocker(Anime_Content__c c) {
        Object report = ShowrunnerSupport.parse(c.Render_Report__c);
        if (report instanceof Map<String, Object>) {
            Object visual = ((Map<String, Object>) report).get('visual_qa');
            if (visual instanceof Map<String, Object>) {
                Object score = ((Map<String, Object>) visual).get('edit_score');
                if (score != null && Decimal.valueOf(String.valueOf(score)) < 7) {
                    return 'the quality checker scored the edit ' + score + '/10 (bar is 7).';
                }
            }
            Object facts = ((Map<String, Object>) report).get('fact_check');
            if (facts instanceof Map<String, Object> && ((Map<String, Object>) facts).get('pass') == false) {
                return 'the fact checker flagged problems.';
            }
        }
        if (c.QA_Issues__c != null && c.QA_Issues__c.containsIgnoreCase('fact checker flagged')) {
            return 'the fact checker flagged problems.';
        }
        return null;
    }
}