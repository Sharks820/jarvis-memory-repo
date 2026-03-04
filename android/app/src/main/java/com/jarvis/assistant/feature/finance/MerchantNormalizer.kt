package com.jarvis.assistant.feature.finance

import javax.inject.Inject
import javax.inject.Singleton

/**
 * Three-tier merchant name normalizer.
 *
 * 1. Static alias map (~50 common merchant variations)
 * 2. Billing prefix stripping (SQ *, TST*, PP*, etc.)
 * 3. Partial match against known merchants
 *
 * Falls back to title-casing the stripped name.
 */
@Singleton
class MerchantNormalizer @Inject constructor() {

    companion object {
        private val STATIC_MAP = mapOf(
            "amzn" to "Amazon", "amazon" to "Amazon", "amzn*mktplace" to "Amazon",
            "apple.com/bill" to "Apple", "apl*apple" to "Apple", "apple inc" to "Apple",
            "google*" to "Google", "google play" to "Google", "goog*" to "Google",
            "netflix" to "Netflix", "netflix.com" to "Netflix",
            "spotify" to "Spotify", "spotify usa" to "Spotify",
            "uber" to "Uber", "uber*trip" to "Uber", "uber*eats" to "Uber Eats",
            "lyft" to "Lyft", "lyft*ride" to "Lyft",
            "doordash" to "DoorDash", "dd doordash" to "DoorDash",
            "grubhub" to "Grubhub", "gh*grubhub" to "Grubhub",
            "starbucks" to "Starbucks", "sbux" to "Starbucks",
            "walmart" to "Walmart", "wal-mart" to "Walmart", "wm supercenter" to "Walmart",
            "target" to "Target", "target.com" to "Target",
            "costco" to "Costco", "costco whse" to "Costco",
            "walgreens" to "Walgreens", "cvs" to "CVS", "cvs/pharmacy" to "CVS",
            "shell oil" to "Shell", "chevron" to "Chevron", "exxonmobil" to "ExxonMobil",
            "venmo" to "Venmo", "paypal" to "PayPal", "cashapp" to "Cash App",
            "hulu" to "Hulu", "disney+" to "Disney+", "disneyplus" to "Disney+",
            "hbo max" to "Max", "hbomax" to "Max",
            "youtube" to "YouTube Premium", "youtubepremium" to "YouTube Premium",
            "paramount+" to "Paramount+", "peacock" to "Peacock",
            "chipotle" to "Chipotle", "mcdonalds" to "McDonald's", "mcdonald's" to "McDonald's",
            "chick-fil-a" to "Chick-fil-A", "chickfila" to "Chick-fil-A",
            "whole foods" to "Whole Foods", "trader joe" to "Trader Joe's",
            "home depot" to "Home Depot", "lowes" to "Lowe's", "lowe's" to "Lowe's",
        )

        private val BILLING_PREFIXES = listOf(
            "sq *", "sq*", "tst*", "tst *", "pp*", "pp *", "paypal *",
            "cke*", "apl*", "goog*", "amzn*", "dd *", "gh*",
        )
    }

    fun normalize(rawMerchant: String): String {
        val cleaned = rawMerchant.trim().lowercase()

        // 1. Static map lookup
        STATIC_MAP[cleaned]?.let { return it }

        // 2. Strip billing prefixes
        var stripped = cleaned
        for (prefix in BILLING_PREFIXES) {
            if (stripped.startsWith(prefix)) {
                stripped = stripped.removePrefix(prefix).trim()
                break
            }
        }

        // 3. Re-check static map after stripping
        STATIC_MAP[stripped]?.let { return it }

        // 4. Partial match on static map keys
        for ((key, canonical) in STATIC_MAP) {
            if (stripped.contains(key) || key.contains(stripped)) {
                return canonical
            }
        }

        // 5. Title case the stripped version
        return stripped.split(" ").joinToString(" ") { word ->
            word.replaceFirstChar { it.uppercase() }
        }
    }
}
