/**
 * Showrunner actions on a rendered Reel: approve it for publishing. The owner wants no holds: remake or approve.
 * The owner trusts the experts: no quality veto in code. qualityBlocker() is reported to the agent as advice.
 * Publishing still goes through the posting limit (protects the Instagram account).
 */
public with sharing class ShowrunnerReelDecision {
    public class Request {
        @InvocableVariable(label='Reel ID' description='External id of the Reel' required=true)
        public String reelId;
        @InvocableVariable(label='Decision' description='approve' required=true)
        public String decision;
        @InvocableVariable(label='Reason' description='Why (shown to the owner)' required=true)
        public String reason;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='What happened')
        public String outcome;
    }

    @InvocableMethod(label='Decide Reel' description='Approve a rendered Reel for publishing. The checkers\' concerns are returned as advice.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        Anime_Content__c c = ShowrunnerSupport.reel(q.reelId);
        if (q.decision == 'approve') {
            if (c.Status__c != 'Rendered') {
                r.outcome = 'Refused: only rendered Reels can be approved (status ' + c.Status__c + ').';
                return new List<Result>{ r };
            }
            c.Approval_Status__c = 'Approved';
            c.Hold_Reason__c = null;
            String concern = qualityBlocker(c);
            update c; // trigger -> publish workflow (which enforces the posting limit)
            ShowrunnerSupport.log('Showrunner', 'Approve Reel', q.reelId, q.reason, false, 'info');
            r.outcome = 'Approved: ' + q.reelId + ' goes to the publish queue.'
                + (concern == null ? '' : ' (Note: ' + concern + ' You chose to post it anyway.)');
        } else {
            r.outcome = 'Refused: the owner wants no holds. Remake it (if remakes are left) or approve your best version.';
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