/**
 * Events -> Showrunner. When a Reel is held by the quality gate or fails, the Showrunner agent is woken
 * (asynchronously) with what happened and decides what to do using its actions.
 * Holds placed by the Showrunner itself ("Showrunner: ...") never re-trigger it.
 */
public with sharing class ShowrunnerEvents {
    public static final String AGENT = 'Senpai_Showrunner';
    @TestVisible
    private static Boolean disabled = false;

    public static void afterUpdate(List<Anime_Content__c> rows, Map<Id, Anime_Content__c> oldMap) {
        if (disabled) {
            return;
        }
        List<String> events = new List<String>();
        for (Anime_Content__c c : rows) {
            Anime_Content__c old = oldMap.get(c.Id);
            Boolean ownHold = c.Hold_Reason__c != null && c.Hold_Reason__c.startsWith('Showrunner:');
            Boolean newlyHeld = c.Status__c == 'Rendered' && c.Approval_Status__c == 'Pending' && c.Hold_Reason__c != null
                && !ownHold && c.Hold_Reason__c != old.Hold_Reason__c;
            Boolean newlyFailed = c.Status__c == 'Failed' && old.Status__c != 'Failed';
            if (newlyHeld) {
                events.add('EVENT reel_held: Reel ' + c.External_Id__c + ' was rendered but held. Hold reason: ' + c.Hold_Reason__c);
            } else if (newlyFailed) {
                events.add('EVENT reel_failed: Reel ' + c.External_Id__c + ' failed. Error: ' + String.valueOf(c.Error_Message__c).abbreviate(600));
            }
        }
        if (!events.isEmpty() && Limits.getQueueableJobs() < Limits.getLimitQueueableJobs()) {
            System.enqueueJob(new Wake(events));
        }
    }

    /** Talks to the agent (one message per event) and logs its answer. */
    public class Wake implements Queueable, Database.AllowsCallouts {
        private final List<String> events;

        public Wake(List<String> events) {
            this.events = events;
        }

        public void execute(QueueableContext context) {
            for (String event : events) {
                String reelId = event.substringBetween('Reel ', ' ');
                String answer;
                try {
                    answer = ask(event + '\nInvestigate with your tools and act now. Finish with a one-paragraph summary of what you decided and why.');
                } catch (Exception e) {
                    answer = 'Agent unavailable: ' + e.getMessage();
                }
                ShowrunnerSupport.log('Showrunner', 'Handled event', reelId, event + '\n\nShowrunner: ' + answer, false, 'info');
            }
        }
    }

    public static String ask(String message) {
        if (Test.isRunningTest()) {
            return answerText('{"type":"Text","value":"test"}');
        }
        Invocable.Action action = Invocable.Action.createCustomAction('generateAiAgentResponse', AGENT);
        action.setInvocationParameter('userMessage', message);
        Invocable.Action.Result result = action.invoke()[0];
        if (!result.isSuccess()) {
            return 'Agent error: ' + JSON.serialize(result.getErrors()).abbreviate(500);
        }
        return answerText(String.valueOf(result.getOutputParameters().get('agentResponse')));
    }

    /** The agent replies {"type":"Text","value":"..."}; return the text. */
    @TestVisible
    private static String answerText(String response) {
        Object parsed = ShowrunnerSupport.parse(response);
        if (parsed instanceof Map<String, Object> && ((Map<String, Object>) parsed).get('value') != null) {
            return String.valueOf(((Map<String, Object>) parsed).get('value'));
        }
        return response;
    }
}
