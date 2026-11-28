/**
 * Showrunner action: rebuild a Reel with the Showrunner's instructions (different show / topic / format / notes).
 * Guardrail: at most ShowrunnerSupport.MAX_REMAKES per Reel; published Reels can't be remade.
 */
public with sharing class ShowrunnerRemakeReel {
    public class Request {
        @InvocableVariable(label='Reel ID' description='External id of the Reel to rebuild' required=true)
        public String reelId;
        @InvocableVariable(label='Instructions' description='What to fix, concretely (passed to the writers and editors)' required=true)
        public String notes;
        @InvocableVariable(label='Show' description='Optional: switch to this anime')
        public String show;
        @InvocableVariable(label='Topic' description='Optional: the exact topic / angle to cover')
        public String topic;
        @InvocableVariable(label='Format' description='Optional: one of the channel formats, e.g. Backstory, Fan Theory, Why It\'s Trending')
        public String format;
    }
    public class Result {
        @InvocableVariable(label='Outcome' description='Whether the remake started, or why not')
        public String outcome;
    }

    @InvocableMethod(label='Remake Reel' description='Rebuilds a held or failed Reel with new instructions (max 2 remakes per Reel).')
    public static List<Result> run(List<Request> requests) {
        Request q = requests[0];
        Result r = new Result();
        Anime_Content__c c = ShowrunnerSupport.reel(q.reelId);
        Integer done = c.Remakes__c == null ? 0 : c.Remakes__c.intValue();
        if (c.Status__c == 'Published' || c.Status__c == 'Publishing') {
            r.outcome = 'Refused: ' + q.reelId + ' is already published.';
            return new List<Result>{ r };
        }
        if (done >= ShowrunnerSupport.MAX_REMAKES) {
            r.outcome = 'Refused: ' + q.reelId + ' was already remade ' + done + ' times (limit ' + ShowrunnerSupport.MAX_REMAKES + '). Hold it and notify the owner instead.';
            return new List<Result>{ r };
        }
        Set<String> formats = new Set<String>();
        for (Schema.PicklistEntry e : Anime_Content__c.Format__c.getDescribe().getPicklistValues()) {
            formats.add(e.getValue());
        }
        Map<String, Object> directive = new Map<String, Object>{ 'notes' => q.notes, 'by' => 'Showrunner', 'at' => Datetime.now() };
        if (String.isNotBlank(q.show)) directive.put('show', q.show.trim());
        if (String.isNotBlank(q.topic)) directive.put('topic', q.topic.trim());
        if (String.isNotBlank(q.format) && formats.contains(q.format.trim())) directive.put('format', q.format.trim());
        c.Showrunner_Directive__c = JSON.serialize(directive).abbreviate(5000);
        c.Remakes__c = done + 1;
        c.Status__c = 'Queued';
        c.Approval_Status__c = 'Pending';
        c.Hold_Reason__c = null;
        c.Error_Message__c = null;
        if (directive.containsKey('format')) c.Format__c = (String) directive.get('format');
        if (directive.containsKey('topic')) c.Topic__c = ((String) directive.get('topic')).abbreviate(255);
        if (directive.containsKey('show')) {
            List<Anime__c> anime = [SELECT Id FROM Anime__c WHERE Name = :q.show.trim() LIMIT 1];
            c.Anime__c = anime.isEmpty() ? null : anime[0].Id;
        }
        update c;
        System.enqueueJob(new ReelDispatchQueueable(new List<Id>{ c.Id }, ReelDispatchQueueable.GENERATE));
        ShowrunnerSupport.log('Showrunner', 'Remake Reel', q.reelId, JSON.serialize(directive), false, 'info');
        r.outcome = 'Remake ' + (done + 1) + ' of ' + ShowrunnerSupport.MAX_REMAKES + ' started for ' + q.reelId + '.';
        return new List<Result>{ r };
    }
}