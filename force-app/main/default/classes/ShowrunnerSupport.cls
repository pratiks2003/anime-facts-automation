/** Shared helpers for the Showrunner's actions: find a Reel, log every agent action. */
public with sharing class ShowrunnerSupport {
    public static final Integer MAX_REMAKES = 2;

    public static Anime_Content__c reel(String externalId) {
        List<Anime_Content__c> rows = [
            SELECT Id, External_Id__c, Name, Slot__c, Format__c, Topic__c, Status__c, Approval_Status__c, Hold_Reason__c,
                Quality_Score__c, QA_Issues__c, Director_Notes__c, Strategy__c, Render_Report__c, Script_JSON__c, Caption__c,
                Video_URL__c, Cover_URL__c, Error_Message__c, Remakes__c, Showrunner_Directive__c, Anime__r.Name, Style__r.Name,
                Dry_Run__c, Attempts__c, CreatedDate
            FROM Anime_Content__c WHERE External_Id__c = :externalId LIMIT 1
        ];
        if (rows.isEmpty()) {
            throw new InstaAgentConfig.InstaAgentException('No Reel with id ' + externalId);
        }
        return rows[0];
    }

    public static void log(String agent, String action, String reelId, String details, Boolean needsOwner, String severity) {
        insert new Agent_Activity__c(
            Name = (action + ' · ' + (reelId == null ? '' : reelId)).abbreviate(80),
            Agent__c = agent, Action__c = action, Reel__c = reelId,
            Details__c = details == null ? null : details.abbreviate(5000),
            Needs_Owner__c = needsOwner == true, Severity__c = severity == null ? 'info' : severity
        );
    }

    public static Object parse(String jsonText) {
        if (String.isBlank(jsonText)) {
            return null;
        }
        try {
            return JSON.deserializeUntyped(jsonText);
        } catch (Exception e) {
            return jsonText.abbreviate(2000);
        }
    }
}
