/**
 * Typed access to the InstaAgent_Settings__c org-default record (the live automation switches).
 */
public with sharing class InstaAgentConfig {
    public static final String TIMEZONE_ID = 'Asia/Kolkata';

    @TestVisible
    private static InstaAgent_Settings__c cached;

    public static InstaAgent_Settings__c get() {
        if (cached == null) {
            InstaAgent_Settings__c s = InstaAgent_Settings__c.getOrgDefaults();
            if (s == null || s.Id == null) {
                // Defaults mirror the field definitions so code works before the record exists.
                s = new InstaAgent_Settings__c(
                    SetupOwnerId = UserInfo.getOrganizationId(),
                    Automation_Enabled__c = false,
                    Two_Reels_Per_Day__c = false,
                    Auto_Approve__c = true,
                    Dry_Run__c = true,
                    Morning_Slot_IST__c = '09:00',
                    Evening_Slot_IST__c = '19:00',
                    GitHub_Repository__c = 'pratiks2003/anime-facts-automation',
                    Generate_Workflow__c = 'reel.yml'
                );
            }
            cached = s;
        }
        return cached;
    }

    public static void save(InstaAgent_Settings__c changes) {
        InstaAgent_Settings__c current = get();
        current.Automation_Enabled__c = changes.Automation_Enabled__c;
        current.Two_Reels_Per_Day__c = changes.Two_Reels_Per_Day__c;
        current.Auto_Approve__c = changes.Auto_Approve__c;
        current.Dry_Run__c = changes.Dry_Run__c;
        if (String.isNotBlank(changes.Morning_Slot_IST__c)) {
            current.Morning_Slot_IST__c = validTime(changes.Morning_Slot_IST__c);
        }
        if (String.isNotBlank(changes.Evening_Slot_IST__c)) {
            current.Evening_Slot_IST__c = validTime(changes.Evening_Slot_IST__c);
        }
        upsert current;
        cached = current;
    }

    public static String repository() {
        return get().GitHub_Repository__c;
    }

    /** Accepts HH:mm (24h); throws otherwise. */
    public static String validTime(String value) {
        if (value == null || !Pattern.matches('([01][0-9]|2[0-3]):[0-5][0-9]', value.trim())) {
            throw new InstaAgentException('Time must be HH:mm in 24-hour format, got: ' + value);
        }
        return value.trim();
    }

    public class InstaAgentException extends Exception {
    }
}
