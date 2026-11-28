/**
 * Events -> Showrunner. Due slots go to the Planner; when a Reel is held by the quality gate or fails, the Showrunner agent is woken
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

    /** Scheduled slots go to the Showrunner's Planner first; if it does not start rendering, the slot renders anyway. */
    public static void planSlots(List<Id> contentIds) {
        if (contentIds.isEmpty()) {
            return;
        }
        System.enqueueJob(new Plan(contentIds));
    }

    public class Plan implements Queueable, Database.AllowsCallouts {
        private final List<Id> ids;

        public Plan(List<Id> ids) {
            this.ids = ids;
        }

        public void execute(QueueableContext context) {
            List<Id> fallback = new List<Id>();
            Map<String, String> answers = new Map<String, String>();  // logged after all callouts (no DML before a callout)
            for (Anime_Content__c c : [SELECT Id, External_Id__c, Slot__c, Scheduled_For__c, Format__c FROM Anime_Content__c WHERE Id IN :ids]) {
                String answer;
                try {
                    answer = disabled ? 'disabled' : ask('EVENT plan_due: Reel ' + c.External_Id__c + ' is the ' + c.Slot__c + ' slot, posting at '
                        + c.Scheduled_For__c.format('HH:mm', InstaAgentConfig.TIMEZONE_ID) + ' IST (rotation suggests ' + c.Format__c
                        + '). Decide what this Reel should be to get the most views right now, then submit_plan.');
                } catch (Exception e) {
                    answer = 'Agent unavailable: ' + e.getMessage();
                }
                answers.put(c.External_Id__c, answer);
                Anime_Content__c now = [SELECT Showrunner_Directive__c, Status__c FROM Anime_Content__c WHERE Id = :c.Id];
                if (String.isBlank(now.Showrunner_Directive__c) && now.Status__c == 'Queued') {
                    fallback.add(c.Id);
                }
            }
            if (!fallback.isEmpty() && !Test.isRunningTest()) {  // never miss a slot because the agent was busy
                System.enqueueJob(new ReelDispatchQueueable(fallback, ReelDispatchQueueable.GENERATE));
            }
            for (String reelId : answers.keySet()) {
                ShowrunnerSupport.log('Planner', 'Handled event', reelId, 'EVENT plan_due ' + reelId + '\n\nShowrunner: ' + answers.get(reelId)
                    + (fallback.isEmpty() ? '' : '\n(No plan submitted for some slots: rendered with the default plan.)'), false, 'info');
            }
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
