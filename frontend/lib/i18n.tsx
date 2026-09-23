"use client";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { getUserProfile, isAuthenticated } from "./auth";
import { SUPPORTED_LOCALES, LOCALE_COOKIE_KEY, type Locale } from "./locale";

// Re-exported for existing "@/lib/i18n" imports. Server components (e.g.
// app/layout.tsx) must import these two directly from "@/lib/locale"
// instead — a "use client" module's exports, re-exports included, become
// client-reference proxies when read from the server, and calling
// .includes() on one throws at request time.
export { SUPPORTED_LOCALES, LOCALE_COOKIE_KEY, type Locale };

const LOCALE_STORAGE_KEY = "dms_locale";
const CACHE_KEY_PREFIX = "dms_i18n_cache_";

const DEVANAGARI_DIGITS = ["०", "१", "२", "३", "४", "५", "६", "७", "८", "९"];

export function toDevanagariDigits(input: number | string): string {
  return String(input).replace(/[0-9]/g, (d) => DEVANAGARI_DIGITS[parseInt(d, 10)]);
}

export function fromDevanagariDigits(input: string): string {
  return input.replace(/[०-९]/g, (d) => String(d.charCodeAt(0) - 0x0966));
}

export const STATIC_TRANSLATIONS: Record<Locale, Record<string, string>> = {
  en: {
    // Auth
    "auth.login.title": "Sign in",
    "auth.login.email_label": "Email",
    "auth.login.password_label": "Password",
    "auth.login.submit": "Sign in",
    "auth.login.forgot_password": "Forgot password?",
    "auth.login.no_account": "Don't have an account?",
    "auth.login.signup_link": "Sign up",
    "auth.signup.title": "Create your account",
    "auth.signup.full_name_label": "Full name",
    "auth.signup.email_label": "Email",
    "auth.signup.password_label": "Password",
    "auth.signup.submit": "Create Account",
    "auth.signup.have_account": "Already have an account?",
    "auth.signup.login_link": "Sign in",
    "auth.forgot.title": "Reset your password",
    "auth.forgot.email_label": "Email",
    "auth.forgot.submit": "Send reset link",
    "auth.forgot.back_to_login": "Back to sign in",

    // Header & Navigation
    "header.search_placeholder": "Search anything with Stark AI...",
    "header.account_menu": "Account",
    "header.profile_analytics": "Profile & Analytics",
    "header.verification_workbench": "Verification Workbench",
    "header.completeness_dashboard": "Completeness Dashboard",
    "header.entity_360": "Entity 360",
    "header.logout": "Log Out",
    "header.search_settings": "Search Settings",
    "header.reranker_strategy": "Reranker strategy",
    "header.ai_summary": "AI Summary generation",
    "header.language": "Language",
    "header.role": "Role",
    "header.administration": "Administration",
    "header.admin_panel": "Admin Panel",
    "header.users_roles": "Users & Roles",
    "header.departments": "Departments",
    "header.form_templates": "Form Templates",
    "header.admin_settings": "Settings",
    "role.records_officer": "Records Officer",
    "role.operator": "Operator",
    "role.department_head": "Department Head",
    "role.legal_counsel": "Legal Counsel",
    "role.it_admin": "IT Admin",
    "role.auditor": "Auditor",
    "rbac.no_access_title": "You don't have access",
    "rbac.read_only_notice": "Your role can view this queue but not review, confirm, or edit facts.",

    // Drive Sidebar & Views
    "drive.nav.home": "Home",
    "drive.nav.chat": "AI Chat",
    "drive.nav.my_drive": "My Drive",
    "drive.nav.starred": "Starred",
    "drive.nav.trash": "Bin",
    "drive.nav.new": "New",
    "drive.nav.new_folder": "New folder",
    "drive.nav.file_upload": "File upload",
    "drive.nav.connect_device": "Connect a device",
    "drive.storage.title": "Storage",
    "drive.storage.used": "used",
    "drive.search.placeholder_ai": "Ask AI anything about your DMS documents...",
    "drive.search.placeholder_standard": "Search in DMS...",
    "drive.search.ai_toggle": "AI",
    "drive.view.switch_to_list": "Switch to List view",
    "drive.view.switch_to_grid": "Switch to Grid view",

    // Workbench
    "workbench.title": "Verification Workbench",
    "workbench.tab.needs_review": "Needs Review",
    "workbench.tab.needs_review_desc": "Every field waiting on a human decision, sorted worst-confidence first — not only low-scoring ones.",
    "workbench.tab.handwritten": "Handwritten",
    "workbench.tab.handwritten_desc": 'Fields the system read from handwriting rather than print. Excludes margin notes — see "Marginalia".',
    "workbench.tab.marginalia": "Marginalia",
    "workbench.tab.marginalia_desc": "Handwritten notes found outside any known field on the page (margin notes, stamps, annotations).",
    "workbench.tab.join_mismatches": "Join Mismatches",
    "workbench.tab.join_mismatches_desc": "A two-page entry the system couldn't reliably match left-to-right — needs a human to pair the halves.",
    "workbench.tab.continuation_unclear": "Continuation Unclear",
    "workbench.tab.continuation_unclear_desc": "A page pair the system couldn't confidently classify as the same table continuing, a side-by-side spread, or unrelated. Your answer here applies automatically to every future document with this same page shape.",
    "workbench.sentinel.marginalia": "Handwritten margin note",
    "workbench.sentinel.join_mismatch": "Table join couldn't be matched",
    "workbench.sentinel.stitch_ambiguous": "Table continuation unclear",
    "workbench.btn.claim": "Claim",
    "workbench.btn.release": "Release",
    "workbench.btn.confirm": "Confirm (Verified)",
    "workbench.btn.edit_save": "Edit & Save",
    "workbench.btn.dismiss": "Dismiss",
    "workbench.btn.revert": "Revert to Machine",
    "workbench.btn.view_source": "View Source Region",
    "workbench.btn.batch_confirm": "Batch Confirm Claimed",
    "workbench.btn.claim_next": "Claim Next 10",
    "workbench.btn.refresh": "Refresh Queue",
    "workbench.label.filter_folder": "Filter by folder:",
    "workbench.label.all_folders": "All Folders",
    "workbench.label.sort_by": "Sort by:",
    "workbench.sort.lowest_confidence": "Lowest Confidence",
    "workbench.sort.highest_confidence": "Highest Confidence",
    "workbench.sort.newest": "Newest First",
    "workbench.sort.oldest": "Oldest First",
    "workbench.label.field": "Field",
    "workbench.label.extracted_value": "Extracted Value",
    "workbench.label.confidence": "Confidence",
    "workbench.label.document": "Document",
    "workbench.label.status": "Status",
    "workbench.label.actions": "Actions",
    "workbench.label.keyboard_shortcuts": "Keyboard Shortcuts",
    "workbench.empty_queue": "No items in this queue",
    "workbench.queue_all_caught_up": "All caught up! There are no pending items in this category.",
    "workbench.deskew.deskewed": "Straightened (Deskewed)",
    "workbench.deskew.original": "Original Scan",
    "workbench.deskew.skew_badge": "Skew",

    // Entity 360
    "entities.title": "Entity 360",
    "entities.search_placeholder": "Search entity by name or ID...",
    "entities.overview": "Entity Overview",
    "entities.records": "Associated Records",
    "entities.linked_entities": "Linked Entities",
    "entities.linked_facts": "Linked Facts",
    "entities.amendment_history": "Amendment History",
    "entities.status.auto_linked": "Auto-linked",
    "entities.status.needs_confirmation": "Needs confirmation",
    "entities.status.verified": "Verified",
    "entities.status.reverted": "Reverted",
    "entities.btn.confirm_edge": "Confirm Link",
    "entities.btn.revert_edge": "Revert Link",
    "entities.no_entities_found": "No entities found",
    "entities.select_entity_prompt": "Select an entity to view relationships and records",

    // Completeness Dashboard
    "completeness.title": "Completeness Dashboard",
    "completeness.corpus_folder": "Corpus Folder",
    "completeness.load_dashboard": "Load Dashboard",
    "completeness.select_folder": "Select Folder",
    "completeness.metrics.total_docs": "Total Documents",
    "completeness.metrics.total_pages": "Total Pages",
    "completeness.metrics.failed_pages": "Failed Pages",
    "completeness.metrics.ocr_data_loss": "OCR Data Loss",
    "completeness.metrics.verified_facts": "Verified Facts",
    "completeness.metrics.unverified_facts": "Unverified Facts",
    "completeness.metrics.missing_fields": "Missing Required Fields",
    "completeness.drilldown.title": "Drilldown Details",
    "completeness.drilldown.no_issues": "No issues found in this category",

    // Document Preview & Facts
    "preview.extracted_facts": "Extracted Facts",
    "preview.table_view": "Table View",
    "preview.list_view": "List View",
    "preview.ask_ai": "Ask AI",
    "preview.confidence": "Confidence",
    "preview.page": "Page",
    "preview.verified": "Verified",
    "preview.unverified": "Unverified",
    "preview.save_edit": "Save Edit",
    "preview.cancel_edit": "Cancel",
    "preview.stitch_notice": "Cross-page stitch resolved",

    // Common
    "common.new_folder": "New folder",
    "common.upload": "Upload",
    "common.save": "Save",
    "common.cancel": "Cancel",
    "common.delete": "Delete",
    "common.confirm": "Confirm",
    "common.loading": "Loading...",
    "common.search": "Search",
    "common.close": "Close",
    "common.create": "Create",
    "common.rename": "Rename",
    "common.move": "Move",
    "common.download": "Download",
    "common.share": "Share",
    "common.no_results": "No results found",
    "common.back": "Back",
    "common.next": "Next",
    "common.edit": "Edit",
    "common.view": "View",
    "common.ok": "OK",
    "common.yes": "Yes",
    "common.no": "No",
    "common.error": "Error",
    "common.success": "Success",
    "common.warning": "Warning",
    "common.actions": "Actions",
    "common.status": "Status",
    "common.filter": "Filter",
    "common.sort": "Sort",
    "common.refresh": "Refresh",
    "common.apply": "Apply",
    "common.reset": "Reset",
    "common.clear": "Clear",
    "common.offline_notice": "You are currently offline. Changes will sync when reconnected.",
  },
  mr: {
    // Auth
    "auth.login.title": "साइन इन करा",
    "auth.login.email_label": "ईमेल",
    "auth.login.password_label": "पासवर्ड",
    "auth.login.submit": "साइन इन करा",
    "auth.login.forgot_password": "पासवर्ड विसरलात?",
    "auth.login.no_account": "खाते नाही?",
    "auth.login.signup_link": "साइन अप करा",
    "auth.signup.title": "तुमचे खाते तयार करा",
    "auth.signup.full_name_label": "पूर्ण नाव",
    "auth.signup.email_label": "ईमेल",
    "auth.signup.password_label": "पासवर्ड",
    "auth.signup.submit": "खाते तयार करा",
    "auth.signup.have_account": "आधीच खाते आहे?",
    "auth.signup.login_link": "साइन इन करा",
    "auth.forgot.title": "तुमचा पासवर्ड रीसेट करा",
    "auth.forgot.email_label": "ईमेल",
    "auth.forgot.submit": "रीसेट लिंक पाठवा",
    "auth.forgot.back_to_login": "साइन इनवर परत जा",

    // Header & Navigation
    "header.search_placeholder": "स्टार्क AI सह काहीही शोधा...",
    "header.account_menu": "खाते",
    "header.profile_analytics": "प्रोफाइल आणि विश्लेषण",
    "header.verification_workbench": "पडताळणी कार्यक्षेत्र",
    "header.completeness_dashboard": "पूर्णता डॅशबोर्ड",
    "header.entity_360": "एंटिटी ३६०",
    "header.logout": "लॉग आउट करा",
    "header.search_settings": "शोध सेटिंग्ज",
    "header.reranker_strategy": "रीरँकर रणनीती",
    "header.ai_summary": "AI सारांश निर्मिती",
    "header.language": "भाषा",
    "header.role": "भूमिका",
    "header.administration": "प्रशासन",
    "header.admin_panel": "प्रशासन पॅनेल",
    "header.users_roles": "वापरकर्ते आणि भूमिका",
    "header.departments": "विभाग",
    "header.form_templates": "फॉर्म साचे",
    "header.admin_settings": "सेटिंग्ज",
    "role.records_officer": "अभिलेख अधिकारी",
    "role.operator": "ऑपरेटर",
    "role.department_head": "विभाग प्रमुख",
    "role.legal_counsel": "कायदेशीर सल्लागार",
    "role.it_admin": "IT प्रशासक",
    "role.auditor": "लेखापरीक्षक",
    "rbac.no_access_title": "तुम्हाला प्रवेश नाही",
    "rbac.read_only_notice": "तुमची भूमिका ही रांग पाहू शकते, परंतु तथ्ये तपासू, पुष्टी करू किंवा संपादित करू शकत नाही.",

    // Drive Sidebar & Views
    "drive.nav.home": "मुख्यपृष्ठ",
    "drive.nav.chat": "AI चॅट",
    "drive.nav.my_drive": "माझे ड्राइव्ह",
    "drive.nav.starred": "तारांकित",
    "drive.nav.trash": "कचरा पेटी (Bin)",
    "drive.nav.new": "नवीन",
    "drive.nav.new_folder": "नवीन फोल्डर",
    "drive.nav.file_upload": "फाइल अपलोड",
    "drive.nav.connect_device": "डिव्हाइस कनेक्ट करा",
    "drive.storage.title": "स्टोरेज",
    "drive.storage.used": "वापरले",
    "drive.search.placeholder_ai": "तुमच्या DMS दस्तऐवजांबद्दल AI ला काहीही विचारा...",
    "drive.search.placeholder_standard": "DMS मध्ये शोधा...",
    "drive.search.ai_toggle": "AI",
    "drive.view.switch_to_list": "यादी दृश्यावर स्विच करा",
    "drive.view.switch_to_grid": "ग्रीड दृश्यावर स्विच करा",

    // Workbench
    "workbench.title": "पडताळणी कार्यक्षेत्र",
    "workbench.tab.needs_review": "पुनरावलोकन आवश्यक",
    "workbench.tab.needs_review_desc": "प्रत्येक फील्ड जे मानवी निर्णयाची वाट पाहत आहे, सर्वात कमी विश्वासापासून क्रमवारी लावली आहे.",
    "workbench.tab.handwritten": "हस्तलिखित",
    "workbench.tab.handwritten_desc": "सिस्टमने मुद्रित मजकुराऐवजी हस्तलिखितातून वाचलेले फील्ड्स.",
    "workbench.tab.marginalia": "मार्जिन नोट्स (टीपा)",
    "workbench.tab.marginalia_desc": "पृष्ठावरील कोणत्याही ज्ञात फील्डबाहेर आढळलेल्या हस्तलिखित नोंदी (मार्जिन नोट्स, शिक्के, टिप्पण्या).",
    "workbench.tab.join_mismatches": "जोड विसंगती",
    "workbench.tab.join_mismatches_desc": "दोन पृष्ठांची नोंद जी डावीकडून उजवीकडे जुळवता आली नाही — मानवी पडताळणी आवश्यक.",
    "workbench.tab.continuation_unclear": "सातत्य अस्पष्ट",
    "workbench.tab.continuation_unclear_desc": "पृष्ठ जोडी ज्याचे वर्गीकरण अस्पष्ट आहे — तेच सारणी सुरू आहे, दोन-पृष्ठ स्प्रेड आहे किंवा असंबंधित आहे.",
    "workbench.sentinel.marginalia": "हस्तलिखित मार्जिन टीप",
    "workbench.sentinel.join_mismatch": "सारणी जोड जुळवता आला नाही",
    "workbench.sentinel.stitch_ambiguous": "सारणी सातत्य अस्पष्ट",
    "workbench.btn.claim": "स्वीकारा (Claim)",
    "workbench.btn.release": "मुक्त करा (Release)",
    "workbench.btn.confirm": "पुष्टी करा (प्रमाणित)",
    "workbench.btn.edit_save": "संपादित करा आणि जतन करा",
    "workbench.btn.dismiss": "नाकारा (Dismiss)",
    "workbench.btn.revert": "मूळ स्थितीत आणा",
    "workbench.btn.view_source": "मूळ स्रोत क्षेत्र पाहा",
    "workbench.btn.batch_confirm": "स्वीकारलेल्यांची एकत्रित पुष्टी करा",
    "workbench.btn.claim_next": "पुढील १० स्वीकारा",
    "workbench.btn.refresh": "रांग रीफ्रेश करा",
    "workbench.label.filter_folder": "फोल्डरनुसार फिल्टर करा:",
    "workbench.label.all_folders": "सर्व फोल्डर्स",
    "workbench.label.sort_by": "यानुसार क्रमवारी लावा:",
    "workbench.sort.lowest_confidence": "सर्वात कमी विश्वास",
    "workbench.sort.highest_confidence": "सर्वात जास्त विश्वास",
    "workbench.sort.newest": "नवीनतम प्रथम",
    "workbench.sort.oldest": "जुने प्रथम",
    "workbench.label.field": "फील्ड",
    "workbench.label.extracted_value": "काढलेले मूल्य",
    "workbench.label.confidence": "विश्वासार्हता",
    "workbench.label.document": "दस्तऐवज",
    "workbench.label.status": "स्थिती",
    "workbench.label.actions": "क्रिया",
    "workbench.label.keyboard_shortcuts": "कीबोर्ड शॉर्टकट",
    "workbench.empty_queue": "या रांगेत कोणतीही बाब नाही",
    "workbench.queue_all_caught_up": "सर्व काम पूर्ण झाले! या श्रेणीत प्रलंबित नोंदी नाहीत.",
    "workbench.deskew.deskewed": "सरळ केलेले (Deskewed)",
    "workbench.deskew.original": "मूळ स्कॅन",
    "workbench.deskew.skew_badge": "तिरपेपणा",

    // Entity 360
    "entities.title": "एंटिटी ३६०",
    "entities.search_placeholder": "नावाने किंवा आयडीने एंटिटी शोधा...",
    "entities.overview": "एंटिटी आढावा",
    "entities.records": "संबंधित रेकॉर्ड्स",
    "entities.linked_entities": "जोडलेल्या एंटिटीज",
    "entities.linked_facts": "जोडलेले तथ्य (Facts)",
    "entities.amendment_history": "दुरुस्ती इतिहास",
    "entities.status.auto_linked": "स्वयं-जोडलेले",
    "entities.status.needs_confirmation": "पुष्टीकरण आवश्यक",
    "entities.status.verified": "प्रमाणित",
    "entities.status.reverted": "पूर्ववत केलेले",
    "entities.btn.confirm_edge": "जोडणीची पुष्टी करा",
    "entities.btn.revert_edge": "जोडणी पूर्ववत करा",
    "entities.no_entities_found": "कोणतीही एंटिटी आढळली नाही",
    "entities.select_entity_prompt": "संबंध आणि रेकॉर्ड पाहण्यासाठी एंटिटी निवडा",

    // Completeness Dashboard
    "completeness.title": "पूर्णता डॅशबोर्ड",
    "completeness.corpus_folder": "कॉर्पस फोल्डर",
    "completeness.load_dashboard": "डॅशबोर्ड लोड करा",
    "completeness.select_folder": "फोल्डर निवडा",
    "completeness.metrics.total_docs": "एकूण दस्तऐवज",
    "completeness.metrics.total_pages": "एकूण पृष्ठे",
    "completeness.metrics.failed_pages": "अयशस्वी पृष्ठे",
    "completeness.metrics.ocr_data_loss": "OCR डेटा हानी",
    "completeness.metrics.verified_facts": "प्रमाणित तथ्य",
    "completeness.metrics.unverified_facts": "अप्रमाणित तथ्य",
    "completeness.metrics.missing_fields": "आवश्यक फील्ड गहाळ",
    "completeness.drilldown.title": "सखोल तपशील",
    "completeness.drilldown.no_issues": "या श्रेणीत कोणतीही समस्या आढळली नाही",

    // Document Preview & Facts
    "preview.extracted_facts": "काढलेले तथ्य (Extracted Facts)",
    "preview.table_view": "सारणी दृश्य",
    "preview.list_view": "यादी दृश्य",
    "preview.ask_ai": "AI ला विचारा",
    "preview.confidence": "विश्वासार्हता",
    "preview.page": "पृष्ठ",
    "preview.verified": "प्रमाणित",
    "preview.unverified": "अप्रमाणित",
    "preview.save_edit": "बदल जतन करा",
    "preview.cancel_edit": "रद्द करा",
    "preview.stitch_notice": "पृष्ठ-सातत्य जोडले गेले",

    // Common
    "common.new_folder": "नवीन फोल्डर",
    "common.upload": "अपलोड करा",
    "common.save": "जतन करा",
    "common.cancel": "रद्द करा",
    "common.delete": "हटवा",
    "common.confirm": "पुष्टी करा",
    "common.loading": "लोड होत आहे...",
    "common.search": "शोधा",
    "common.close": "बंद करा",
    "common.create": "तयार करा",
    "common.rename": "पुनर्नामित करा",
    "common.move": "हलवा",
    "common.download": "डाउनलोड करा",
    "common.share": "शेअर करा",
    "common.no_results": "कोणतेही निकाल आढळले नाहीत",
    "common.back": "मागे",
    "common.next": "पुढे",
    "common.edit": "संपादित करा",
    "common.view": "पाहा",
    "common.ok": "ठीक आहे",
    "common.yes": "होय",
    "common.no": "नाही",
    "common.error": "त्रुटी",
    "common.success": "यशस्वी",
    "common.warning": "चेतावणी",
    "common.actions": "क्रिया",
    "common.status": "स्थिती",
    "common.filter": "फिल्टर",
    "common.sort": "क्रमवारी",
    "common.refresh": "रीफ्रेश करा",
    "common.apply": "लागू करा",
    "common.reset": "रीसेट करा",
    "common.clear": "साफ करा",
    "common.offline_notice": "तुम्ही सध्या ऑफलाइन आहात. पुन्हा कनेक्ट झाल्यावर बदल सिंक होतील.",
  },
};

function writeCookieLocale(locale: Locale): void {
  if (typeof document === "undefined") return;
  document.cookie = `${LOCALE_COOKIE_KEY}=${locale}; path=/; max-age=31536000; SameSite=Lax`;
}

function readCachedTranslations(locale: Locale): Record<string, string> | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(CACHE_KEY_PREFIX + locale);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeCachedTranslations(locale: Locale, data: Record<string, string>): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(CACHE_KEY_PREFIX + locale, JSON.stringify(data));
  } catch {
    // best-effort cache; storage full/unavailable is not fatal
  }
}

function readInitialLocale(): Locale {
  if (typeof window === "undefined") return "en";
  const profile = getUserProfile();
  if (profile?.locale && (SUPPORTED_LOCALES as readonly string[]).includes(profile.locale)) {
    return profile.locale as Locale;
  }
  const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
  if (stored && (SUPPORTED_LOCALES as readonly string[]).includes(stored)) {
    return stored as Locale;
  }
  const cookieMatch = document.cookie.match(/(?:^|; )dms_locale=([^;]*)/);
  const cookieValue = cookieMatch ? decodeURIComponent(cookieMatch[1]) : null;
  if (cookieValue && (SUPPORTED_LOCALES as readonly string[]).includes(cookieValue)) {
    return cookieValue as Locale;
  }
  return "en";
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, fallback: string) => string;
  isReady: boolean;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");
  const [translations, setTranslations] = useState<Record<string, string>>({});
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    setLocaleState(readInitialLocale());

    if (isAuthenticated()) {
      api.auth
        .getProfile()
        .then((data) => {
          if (data?.locale && (SUPPORTED_LOCALES as readonly string[]).includes(data.locale)) {
            setLocaleState(data.locale as Locale);
          }
        })
        .catch(() => {
          // offline/unreachable — the synchronous read above already stands
        });
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const cached = readCachedTranslations(locale);
    if (cached) {
      setTranslations(cached);
      setIsReady(true);
    }

    api.i18n
      .getTranslations(locale)
      .then((data) => {
        if (cancelled || !data || typeof data !== "object") return;
        setTranslations(data);
        writeCachedTranslations(locale, data);
        setIsReady(true);
      })
      .catch(() => {
        // Network/offline: fall back to whatever was cached (or static dict)
        setIsReady(true);
      });

    if (typeof document !== "undefined") {
      document.documentElement.lang = locale;
      document.documentElement.classList.toggle("font-devanagari", locale === "mr");
      writeCookieLocale(locale);
    }

    return () => {
      cancelled = true;
    };
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    if (typeof window !== "undefined") {
      localStorage.setItem(LOCALE_STORAGE_KEY, next);
    }
    if (isAuthenticated()) {
      api.auth.updateLocale(next).catch(() => {
        // Best-effort persistence
      });
    }
  }, []);

  const t = useCallback(
    (key: string, fallback: string) => {
      return translations[key] ?? STATIC_TRANSLATIONS[locale]?.[key] ?? fallback;
    },
    [translations, locale]
  );

  const value = useMemo(
    () => ({ locale, setLocale, t, isReady }),
    [locale, setLocale, t, isReady]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    throw new Error("useI18n must be used within an I18nProvider");
  }
  return ctx;
}
