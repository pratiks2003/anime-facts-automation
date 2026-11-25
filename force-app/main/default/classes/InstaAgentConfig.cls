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
                    Generate_Workflow__c = 'reel.yml',
                    Quality_Threshold__c = 8,
                    Auto_Reply_Comments__c = true
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
        current.Trial_Reels_Enabled__c = changes.Trial_Reels_Enabled__c == true;
        current.Auto_Reply_Comments__c = changes.Auto_Reply_Comments__c != false;
        if (String.isNotBlank(changes.Morning_Slot_IST__c)) {
            current.Morning_Slot_IST__c = validTime(changes.Morning_Slot_IST__c);
        }
        if (String.isNotBlank(changes.Evening_Slot_IST__c)) {
            current.Evening_Slot_IST__c = validTime(changes.Evening_Slot_IST__c);
        }
        if (changes.Watchlist__c != null) {
            current.Watchlist__c = cleanWatchlist(changes.Watchlist__c);
        }
        if (current.Quality_Threshold__c == null) {
            current.Quality_Threshold__c = 8;
        }
        upsert current;
        cached = current;
    }

    /** "@a, b\nc" -> "a,b,c": lower-case Instagram usernames, de-duplicated, max 255 chars. */
    public static String cleanWatchlist(String raw) {
        List<String> names = new List<String>();
        for (String part : raw.replace('\n', ',').replace(' ', ',').split(',')) {
            String name = part.trim().removeStart('@').toLowerCase();
            if (Pattern.matches('[a-z0-9._]{1,30}', name) && !names.contains(name)) {
                names.add(name);
            }
        }
        return String.join(names, ',').abbreviate(255);
    }

    public static List<String> watchlist() {
        String raw = get().Watchlist__c;
        return String.isBlank(raw) ? new List<String>() : raw.split(',');
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
