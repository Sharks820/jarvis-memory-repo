package com.jarvis.assistant.feature.prescription

import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.MedicationLogDao
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Handles voice queries about medication status, e.g.:
 * - "Did I take my morning meds?"
 * - "Have I taken my medication today?"
 * - "What pills do I still need to take?"
 *
 * Returns a natural language response or null if the query
 * doesn't match medication patterns (allowing callers to fall
 * through to other handlers).
 */
@Singleton
class MedicationVoiceHandler @Inject constructor(
    private val medicationLogDao: MedicationLogDao,
    private val medicationDao: MedicationDao,
) {

    private val gson = Gson()

    private val medicationPattern = Regex(
        """(?i)(did I take|have I taken|medication|meds|pills|medicine|prescription).*(?:today|morning|evening|night|afternoon)?""",
    )

    /**
     * Returns true if the query text matches medication-related patterns.
     * Used by VoiceEngine to route queries before calling [handleQuery].
     */
    fun matchesMedicationQuery(query: String): Boolean {
        return medicationPattern.containsMatchIn(query)
    }

    /**
     * Process a medication-related voice query and return a natural
     * language response about today's medication status.
     *
     * Returns null if the query doesn't match medication patterns.
     */
    suspend fun handleQuery(query: String): String? {
        if (!matchesMedicationQuery(query)) return null

        val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
        val todayLogs = medicationLogDao.getLogsForDate(today)
        val activeMeds = medicationDao.getActiveMedications()

        if (activeMeds.isEmpty()) {
            return "You don't have any active medications configured, sir."
        }

        // Build the list of all scheduled doses for today
        val scheduledDoses = mutableListOf<ScheduledDose>()
        for (med in activeMeds) {
            val times = parseTimes(med.scheduledTimes)
            for (time in times) {
                scheduledDoses.add(ScheduledDose(med.name, med.dosage, time))
            }
        }

        // Filter out logs that are just "pending" -- only count taken/skipped/missed
        val actionedLogs = todayLogs.filter { it.status != "pending" }
        val takenLogs = actionedLogs.filter { it.status == "taken" }

        // Determine which doses are still pending (not yet taken or skipped)
        val takenSet = takenLogs.map { "${it.medicationName}@${it.scheduledTime}" }.toSet()
        val actionedSet = actionedLogs.map { "${it.medicationName}@${it.scheduledTime}" }.toSet()
        val pendingDoses = scheduledDoses.filter {
            "${it.name}@${it.time}" !in actionedSet
        }

        return when {
            takenLogs.size == scheduledDoses.size && pendingDoses.isEmpty() -> {
                val takenList = takenLogs.joinToString(", ") {
                    "${it.medicationName} at ${it.scheduledTime}"
                }
                "Yes sir, you've taken all your medications today. $takenList."
            }

            takenLogs.isNotEmpty() && pendingDoses.isNotEmpty() -> {
                val takenList = takenLogs.joinToString(", ") {
                    "${it.medicationName} at ${it.scheduledTime}"
                }
                val pendingList = pendingDoses.joinToString(", ") {
                    "${it.name} (${it.dosage}) at ${it.time}"
                }
                "You've taken $takenList, but you still need to take $pendingList."
            }

            pendingDoses.isNotEmpty() -> {
                val scheduleList = pendingDoses.joinToString(", ") {
                    "${it.name} (${it.dosage}) at ${it.time}"
                }
                "You haven't logged any doses yet today. You're scheduled for $scheduleList."
            }

            else -> {
                "I don't have enough information about your medication schedule today, sir."
            }
        }
    }

    private fun parseTimes(scheduledTimes: String): List<String> {
        return try {
            val type = object : TypeToken<List<String>>() {}.type
            gson.fromJson(scheduledTimes, type) ?: emptyList()
        } catch (e: Exception) {
            emptyList()
        }
    }

    private data class ScheduledDose(
        val name: String,
        val dosage: String,
        val time: String,
    )
}
