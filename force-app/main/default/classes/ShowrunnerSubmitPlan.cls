/**
 * Showrunner Planner action: decide what a scheduled slot becomes (show, topic, format, creative brief) and
 * start rendering it. The plan is stored as the Reel's Showrunner directive, which the pipeline follows.
 */
public with sharing class ShowrunnerSubmitPlan {
    public class Request {
        @InvocableVariable(label='Reel ID' description='External id of the planned slot, e.g. R-20261129-AM' required=true)
        public String reelId;
        @InvocableVariable(label='Show' description='The anime to cover' required=true)
        public String show;
        @InvocableVariable(label='Topic' description='The exact topic / angle, specific enough to hook in 1.5 seconds' required=true)
        public String topic;
        @InvocableVariable(label='Format' description='One channel format, e.g. Fan Theory, Backstory, Hidden Details, Who Would Win, Ranking, Why It\'s Trending' required=true)
        public String format;
        @InvocableVariable(label='Brief' description='Creative brief for writers and editors: hook idea, what to show, tone, why it will get views' required=true)
        public String brief;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='Whether rendering started')
        public String outcome;
    }

    @InvocableMethod(label='Submit Plan' description='Locks in the plan for a scheduled Reel slot and starts rendering it.')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        Anime_Content__c c = ShowrunnerSupport.reel(q.reelId);
        if (c.Status__c != 'Queued' && c.Status__c != 'Planned') {
            r.outcome = 'Refused: ' + q.reelId + ' is already ' + c.Status__c + '.';
            return new List<Result>{ r };
        }
        Set<String> formats = new Set<String>();
        for (Schema.PicklistEntry e : Anime_Content__c.Format__c.getDescribe().getPicklistValues()) {
            formats.add(e.getValue());
        }
        Map<String, Object> plan = new Map<String, Object>{
            'show' => q.show == null ? null : q.show.trim(), 'topic' => q.topic, 'notes' => q.brief,
            'by' => 'Showrunner Planner', 'at' => Datetime.now()
        };
        if (q.format != null && formats.contains(q.format.trim())) {
            plan.put('format', q.format.trim());
            c.Format__c = q.format.trim();
        }
        c.Showrunner_Directive__c = JSON.serialize(plan).abbreviate(5000);
        c.Topic__c = q.topic == null ? c.Topic__c : q.topic.abbreviate(255);
        List<Anime__c> anime = [SELECT Id FROM Anime__c WHERE Name = :(String) plan.get('show') LIMIT 1];
        if (!anime.isEmpty()) {
            c.Anime__c = anime[0].Id;
        }
        update c;
        System.enqueueJob(new ReelDispatchQueueable(new List<Id>{ c.Id }, ReelDispatchQueueable.GENERATE));
        ShowrunnerSupport.log('Planner', 'Submit Plan', q.reelId, JSON.serialize(plan), false, 'info');
        r.outcome = 'Planned and rendering: ' + q.show + ' / ' + q.format + ' / ' + q.topic;
        return new List<Result>{ r };
    }
}