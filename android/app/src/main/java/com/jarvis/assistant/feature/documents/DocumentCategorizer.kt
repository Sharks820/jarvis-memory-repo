package com.jarvis.assistant.feature.documents

import javax.inject.Inject
import javax.inject.Singleton

/**
 * Rule-based document categorization using keyword matching on OCR text.
 *
 * Priority order (highest first): id > medical > insurance > warranty > receipt > other.
 * IDs and medical documents are most important to categorize correctly.
 */
@Singleton
class DocumentCategorizer @Inject constructor() {

    fun categorize(ocrText: String): String {
        val text = ocrText.lowercase()

        // Priority 1: ID documents
        val idKeywords = listOf(
            "driver license", "identification", "passport", "social security",
            "date of birth", "dob", "id number", "license number",
        )
        if (idKeywords.any { it in text }) return CATEGORY_ID

        // Priority 2: Medical
        val medicalKeywords = listOf(
            "prescription", "diagnosis", "patient", "doctor", "physician",
            "medication", "dosage", "hospital", "clinic", "lab results",
            "blood", "insurance claim",
        )
        if (medicalKeywords.any { it in text }) return CATEGORY_MEDICAL

        // Priority 3: Insurance
        val insuranceKeywords = listOf(
            "insurance", "policy", "premium", "deductible", "coverage",
            "claim", "beneficiary", "insured",
        )
        if (insuranceKeywords.any { it in text }) return CATEGORY_INSURANCE

        // Priority 4: Warranty
        val warrantyKeywords = listOf(
            "warranty", "guarantee", "coverage", "serial number",
            "model number", "valid until", "expires",
        )
        if (warrantyKeywords.any { it in text }) return CATEGORY_WARRANTY

        // Priority 5: Receipt
        val receiptKeywords = listOf(
            "receipt", "total", "subtotal", "tax", "purchase", "transaction",
            "order #", "item", "qty", "amount due", "change due",
            "visa", "mastercard", "paid",
        )
        if (receiptKeywords.any { it in text }) return CATEGORY_RECEIPT

        // Default fallback
        return CATEGORY_OTHER
    }

    fun getAllCategories(): List<String> = listOf(
        CATEGORY_RECEIPT,
        CATEGORY_WARRANTY,
        CATEGORY_ID,
        CATEGORY_MEDICAL,
        CATEGORY_INSURANCE,
        CATEGORY_OTHER,
    )

    companion object {
        const val CATEGORY_RECEIPT = "receipt"
        const val CATEGORY_WARRANTY = "warranty"
        const val CATEGORY_ID = "id"
        const val CATEGORY_MEDICAL = "medical"
        const val CATEGORY_INSURANCE = "insurance"
        const val CATEGORY_OTHER = "other"
    }
}
