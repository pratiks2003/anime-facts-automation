/**
 * Decides which Reel slots are due and creates their Anime_Content__c records.
 * Idempotent: every slot has a stable External_Id__c (R-yyyyMMdd-AM/PM), so running
 * the planner twice never creates a second Reel for the same slot.
 */
public with sharing class ReelSlotPlanner {
    public static final String MORNING = 'Morning';
    public static final String MIDDAY = 'Midday';
    public static final String EVENING = 'Evening';
    /** Slot -> External Id suffix (R-yyyyMMdd-AM / -MD / -PM). */
    public static final Map<String, String> SUFFIX = new Map<String, String>{ 'Morning' => 'AM', 'Midday' => 'MD', 'Evening' => 'PM' };

    /** Slot name -> HH:mm (IST) for the enabled cadence: 1, 2 or 3 Reels a day. */
    public static Map<String, String> enabledSlots(InstaAgent_Settings__c settings) {
        Map<String, String> enabled = new Map<String, String>{ MORNING => settings.Morning_Slot_IST__c };
        if (settings.Two_Reels_Per_Day__c) {
            if (settings.Three_Reels_Per_Day__c == true) {
                enabled.put(MIDDAY, String.isBlank(settings.Midday_Slot_IST__c) ? '13:30' : settings.Midday_Slot_IST__c);
            }
            enabled.put(EVENING, settings.Evening_Slot_IST__c);
        }
        return enabled;
    }
    /** Dispatch this long before the slot so the Reel is rendered and posted on time. */
    public static final Integer LEAD_MINUTES = 60;
    /** Missed slots younger than this are still produced (e.g. scheduler outage). */
    public static final Integer CATCH_UP_MINUTES = 180;

    public class Slot {
        public String name;
        public Datetime scheduledFor; // GMT instant
        public String externalId;
        public String dayOfWeek; // Mon..Sun in IST
        public Slot(String name, Datetime scheduledFor, String externalId, String dayOfWeek) {
            this.name = name;
            this.scheduledFor = scheduledFor;
            this.externalId = externalId;
            this.dayOfWeek = dayOfWeek;
        }
    }

    /** Slots (today and tomorrow, IST) whose time falls inside [now - catch-up, now + lead]. */
    public static List<Slot> dueSlots(Datetime now, InstaAgent_Settings__c settings) {
        List<Slot> due = new List<Slot>();
        Map<String, String> enabled = enabledSlots(settings);
        Date todayIst = Date.valueOf(now.format('yyyy-MM-dd', InstaAgentConfig.TIMEZONE_ID));
        for (Date day : new List<Date>{ todayIst.addDays(-1), todayIst, todayIst.addDays(1) }) {
            for (String slotName : enabled.keySet()) {
                Datetime at = istToGmt(day, enabled.get(slotName));
                Long minutesUntil = (at.getTime() - now.getTime()) / 60000;
                if (minutesUntil <= LEAD_MINUTES && minutesUntil >= -CATCH_UP_MINUTES) {
                    String ext = 'R-' + at.format('yyyyMMdd', InstaAgentConfig.TIMEZONE_ID) + '-' + SUFFIX.get(slotName);
                    due.add(new Slot(slotName, at, ext, at.format('EEE', InstaAgentConfig.TIMEZONE_ID)));
                }
            }
        }
        return due;
    }

    /** Creates records for due slots that don't exist yet; returns the new records. */
    public static List<Anime_Content__c> planDue(Datetime now) {
        InstaAgent_Settings__c settings = InstaAgentConfig.get();
        List<Slot> slots = dueSlots(now, settings);
        if (slots.isEmpty()) {
            return new List<Anime_Content__c>();
        }
        Set<String> ids = new Set<String>();
        for (Slot s : slots) {
            ids.add(s.externalId);
        }
        Set<String> existing = new Set<String>();
        for (Anime_Content__c c : [SELECT External_Id__c FROM Anime_Content__c WHERE External_Id__c IN :ids]) {
            existing.add(c.External_Id__c);
        }
        Map<String, String> rotation = rotation();
        List<Anime_Content__c> created = new List<Anime_Content__c>();
        for (Slot s : slots) {
            if (existing.contains(s.externalId)) {
                continue;
            }
            String format = rotation.get(s.dayOfWeek + '|' + s.name);
            created.add(
                new Anime_Content__c(
                    Name = (format == null ? 'Fact' : format) + ' · ' + s.scheduledFor.format('d MMM', InstaAgentConfig.TIMEZONE_ID) + ' ' + s.name,
                    External_Id__c = s.externalId,
                    Format__c = format == null ? 'Fact' : format,
                    Slot__c = s.name,
                    Scheduled_For__c = s.scheduledFor,
                    Status__c = 'Queued',
                    Approval_Status__c = 'Pending',
                    Dry_Run__c = settings.Dry_Run__c
                )
            );
        }
        if (!created.isEmpty()) {
            // allOrNone=false: a concurrent run that already inserted a slot just loses the race.
            Database.insert(created, false);
        }
        List<Anime_Content__c> inserted = new List<Anime_Content__c>();
        for (Anime_Content__c c : created) {
            if (c.Id != null) {
                inserted.add(c);
            }
        }
        return inserted;
    }

    /** Day|Slot -> Format from Format_Rotation__mdt. */
    public static Map<String, String> rotation() {
        Map<String, String> result = new Map<String, String>();
        for (Format_Rotation__mdt r : Format_Rotation__mdt.getAll().values()) {
            result.put(r.Day_Of_Week__c + '|' + r.Slot__c, r.Format__c);
        }
        return result;
    }

    /** The GMT instant of HH:mm on `day` in India Standard Time. */
    public static Datetime istToGmt(Date day, String hhmm) {
        List<String> parts = InstaAgentConfig.validTime(hhmm).split(':');
        Datetime asIfGmt = Datetime.newInstanceGmt(day, Time.newInstance(Integer.valueOf(parts[0]), Integer.valueOf(parts[1]), 0, 0));
        Integer offsetMs = TimeZone.getTimeZone(InstaAgentConfig.TIMEZONE_ID).getOffset(asIfGmt);
        return Datetime.newInstance(asIfGmt.getTime() - offsetMs);
    }
}
