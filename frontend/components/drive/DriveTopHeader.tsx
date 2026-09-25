"use client";
import React, { useState } from "react";
import { Sparkles, X, User, LogOut, BarChart3, ChevronDown, ShieldCheck, Settings, Network, UserCog, Building2, LayoutTemplate, SlidersHorizontal, Gauge } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { onKeyActivate } from "@/lib/a11y";
import { useI18n } from "@/lib/i18n";
import { LanguageSwitcher } from "@/components/common/LanguageSwitcher";
import { useRole, displayRole, roleLabel } from "@/lib/permissions";

interface DriveTopHeaderProps {
  onSearch: (query: string, useAi: boolean) => void;
  searchQuery: string;
  onClearSearch: () => void;
  onToggleInfoPanel: () => void;
  showInfoPanel: boolean;
  onNavigateHome?: () => void;
  rerankProvider?: "bgem3" | "cohere";
  onChangeRerankProvider?: (v: "bgem3" | "cohere") => void;
  generateSummary?: boolean;
  onChangeGenerateSummary?: (v: boolean) => void;
}

export function DriveTopHeader({
  onSearch,
  searchQuery,
  onClearSearch,
  onToggleInfoPanel,
  showInfoPanel,
  onNavigateHome,
  rerankProvider = "cohere",
  onChangeRerankProvider,
  generateSummary = true,
  onChangeGenerateSummary,
}: DriveTopHeaderProps) {
  const router = useRouter();
  const { t } = useI18n();
  const [query, setQuery] = useState(searchQuery);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [userName, setUserName] = useState<string>("User");
  const [userEmail, setUserEmail] = useState<string>("");
  const [userInitials, setUserInitials] = useState<string>("U");
  const { role, roleName, can: roleCan } = useRole();
  // Custom roles are named by the admin, so show the name as given; only an
  // old persona string (no role_name yet) goes through translation.
  const displayedRole = displayRole(role);
  const roleText = roleName && roleName !== roleLabel(role)
    ? roleName
    : displayedRole ? t(`role.${displayedRole}`, roleLabel(role)) : roleLabel(role);

  React.useEffect(() => {
    api.auth.getProfile()
      .then((data) => {
        if (data && data.full_name) {
          setUserName(data.full_name);
          setUserEmail(data.email || "");
          const initials = data.full_name
            .split(" ")
            .map((n: string) => n[0])
            .filter(Boolean)
            .join("")
            .toUpperCase()
            .slice(0, 2);
          setUserInitials(initials || "U");
        } else if (data && data.email) {
          setUserEmail(data.email);
          setUserName(data.email.split("@")[0]);
          setUserInitials(data.email.slice(0, 2).toUpperCase());
        }
      })
      .catch(() => { });
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      onSearch(query.trim(), true);
    }
  };

  const handleLogout = () => {
    api.auth.logout();
  };

  return (
    <header className="h-16 px-4 flex items-center justify-between gap-4 bg-gdriveBg select-none border-b border-[#e1e3e1]/40 relative z-30">
      {/* Left: Brand Logo */}
      <div className="flex items-center flex-shrink-0 pl-1">
        <div
          role="button"
          tabIndex={0}
          onClick={() => {
            if (onNavigateHome) onNavigateHome();
          }}
          onKeyDown={onKeyActivate(() => onNavigateHome && onNavigateHome())}
          aria-label="Go to Drive home"
          className="flex items-center cursor-pointer group hover:opacity-95 transition-all"
        >
          <img
            src="/stark-drive.svg"
            alt="Stark Drive Logo"
            className="h-12 md:h-14 lg:h-16 w-auto object-contain transition-transform group-hover:scale-105"
          />
        </div>
      </div>

      {/* Center: Search Bar ("Search anything with Stark AI...") */}
      <form onSubmit={handleSubmit} className="flex-1 max-w-4xl">
        <div className="relative flex items-center group">
          <button type="submit" aria-label="Search" className="absolute left-4 text-[#0d2e5c] hover:text-[#0945a5] z-10 transition-transform group-hover:scale-110">
            <Sparkles className="w-5 h-5 text-[#0d2e5c] animate-pulse" />
          </button>

          <input
            type="text"
            aria-label={t("header.search_placeholder", "Search anything with Stark AI")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("header.search_placeholder", "Search anything with Stark AI...")}
            className="w-full pl-14 pr-12 py-3.5 rounded-full bg-[#edf2fc] text-[#1f1f1f] placeholder:text-[#444746] text-base font-normal border border-[#d3d7dc]/60 hover:bg-[#e4ebf7] hover:border-[#0d2e5c]/30 focus:outline-none focus:bg-white focus:shadow-xl focus:ring-2 focus:ring-[#0d2e5c]/50 focus:border-[#0d2e5c] transition-all duration-300 shadow-inner"
          />

          {query && (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                onClearSearch();
              }}
              className="absolute right-4 p-1.5 rounded-full text-[#444746] hover:bg-[#e1e3e1] transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </form>

      {/* Language switcher */}
      <LanguageSwitcher className="shrink-0" />

      {/* Search Settings: reranker strategy + AI summary on/off — applies to your NEXT search */}
      <div className="relative shrink-0">
        <button
          onClick={() => setSettingsOpen(!settingsOpen)}
          className="flex items-center gap-1.5 p-2.5 rounded-full text-[#444746] hover:bg-[#e1e3e1]/60 transition-colors focus:outline-none"
          title="Search settings — reranker & AI summary"
        >
          <Settings className="w-5 h-5" />
        </button>

        {settingsOpen && (
          <>
            <div role="presentation" className="fixed inset-0 z-40" onClick={() => setSettingsOpen(false)} />
            <div className="absolute right-0 mt-2 w-72 bg-white border border-[#d3d7dc] rounded-xl shadow-xl z-50 p-4 space-y-4 text-[#1f1f1f]">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-[#444746] mb-1">{t("header.search_settings", "Search Settings")}</p>
                <p className="text-[11px] text-[#747775]">Applies to your next global search.</p>
              </div>

              <div>
                <label htmlFor="reranker-strategy" className="text-xs font-medium text-[#444746] mb-1 block">{t("header.reranker_strategy", "Reranker strategy")}</label>
                <select
                  id="reranker-strategy"
                  value={rerankProvider}
                  onChange={(e) => onChangeRerankProvider?.(e.target.value as "bgem3" | "cohere")}
                  className="w-full px-3 py-2 rounded-lg border border-[#d3d7dc] bg-[#edf2fc] text-sm font-medium focus:outline-none focus:ring-1 focus:ring-[#0d2e5c]"
                >
                  <option value="cohere">Cohere (API) — Fast cloud API, no local CPU/GPU load</option>
                  <option value="bgem3">Local (BGE) — no API cost, higher PC load</option>
                </select>
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-[#444746]">{t("header.ai_summary", "AI Summary generation")}</p>
                  <p className="text-[11px] text-[#747775]">Off saves LLM call cost. AI chat still answers when asked.</p>
                </div>
                <button
                  type="button"
                  onClick={() => onChangeGenerateSummary?.(!generateSummary)}
                  className={`relative w-11 h-6 rounded-full shrink-0 transition-colors ${generateSummary ? "bg-[#0d2e5c]" : "bg-[#d3d7dc]"}`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${generateSummary ? "translate-x-5" : ""}`}
                  />
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Right User Badge with Dropdown */}
      <div className="relative">
        <button
          onClick={() => setDropdownOpen(!dropdownOpen)}
          className="flex items-center gap-1.5 p-1 rounded-full hover:bg-[#e1e3e1]/60 transition-colors focus:outline-none"
        >
          <div
            className="w-9 h-9 rounded-full bg-[#c2e7ff] text-[#001d35] font-bold text-xs flex items-center justify-center border border-[#727775] shadow-sm hover:ring-2 hover:ring-[#0d2e5c] transition-all"
            title="Account Menu"
          >
            {userInitials}
          </div>
          <ChevronDown className="w-3.5 h-3.5 text-[#444746]" />
        </button>

        {dropdownOpen && (
          <>
            <div
              role="presentation"
              className="fixed inset-0 z-40"
              onClick={() => setDropdownOpen(false)}
            />
            <div className="absolute right-0 mt-2 w-56 bg-surface border border-borderDark rounded-xl shadow-xl z-50 py-2 animate-fadeIn text-textMain">
              <div className="px-4 py-2.5 border-b border-borderDark/60">
                <p className="text-xs text-textMuted uppercase font-semibold tracking-wider">{t("header.account_menu", "Account")}</p>
                <p className="text-sm font-semibold truncate text-textMain mt-0.5">{userName}</p>
                {userEmail && <p className="text-xs text-textMuted truncate">{userEmail}</p>}
                {role && (
                  <p className="mt-1.5">
                    <span className="sr-only">{t("header.role", "Role")}: </span>
                    <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider bg-[#edf2fc] text-[#0d2e5c] border border-[#0d2e5c]/20">
                      {roleText}
                    </span>
                  </p>
                )}
              </div>

              <div className="py-1">
                <Link
                  href="/profile"
                  onClick={() => setDropdownOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                >
                  <User className="w-4 h-4 text-primary" />
                  <span>{t("header.profile_analytics", "Profile & Analytics")}</span>
                </Link>
                <Link
                  href="/workbench"
                  onClick={() => setDropdownOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                >
                  <ShieldCheck className="w-4 h-4 text-primary" />
                  <span>{t("header.verification_workbench", "Verification Workbench")}</span>
                </Link>
                <Link
                  href="/completeness"
                  onClick={() => setDropdownOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                >
                  <BarChart3 className="w-4 h-4 text-primary" />
                  <span>{t("header.completeness_dashboard", "Completeness Dashboard")}</span>
                </Link>
                <Link
                  href="/entities"
                  onClick={() => setDropdownOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                >
                  <Network className="w-4 h-4 text-primary" />
                  <span>{t("header.entity_360", "Entity 360")}</span>
                </Link>
              </div>

              {/* Admin-only links — each gated by the single permission map
                  (lib/permissions.ts), never by ad-hoc role comparisons. */}
              {(roleCan("analytics.view") || roleCan("users.manage") || roleCan("departments.manage") || roleCan("templates.manage") || roleCan("config.manage")) && (
                <div className="border-t border-borderDark/60 py-1">
                  <p className="px-4 pt-1.5 pb-1 text-[10px] text-textMuted uppercase font-semibold tracking-wider">{t("header.administration", "Administration")}</p>
                  {roleCan("analytics.view") && (
                  <Link
                    href="/admin"
                    onClick={() => setDropdownOpen(false)}
                    className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                  >
                    <Gauge className="w-4 h-4 text-primary" />
                    <span>{t("header.admin_panel", "Admin Panel")}</span>
                  </Link>
                  )}
                  {roleCan("users.manage") && (
                  <Link
                    href="/admin/users"
                    onClick={() => setDropdownOpen(false)}
                    className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                  >
                    <UserCog className="w-4 h-4 text-primary" />
                    <span>{t("header.users_roles", "Users & Roles")}</span>
                  </Link>
                  )}
                  {roleCan("roles.manage") && (
                  <Link
                    href="/admin/roles"
                    onClick={() => setDropdownOpen(false)}
                    className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                  >
                    <ShieldCheck className="w-4 h-4 text-primary" />
                    <span>{t("header.roles", "Roles")}</span>
                  </Link>
                  )}
                  {roleCan("departments.manage") && (
                  <Link
                    href="/admin/departments"
                    onClick={() => setDropdownOpen(false)}
                    className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                  >
                    <Building2 className="w-4 h-4 text-primary" />
                    <span>{t("header.departments", "Departments")}</span>
                  </Link>
                  )}
                  {roleCan("templates.manage") && (
                  <Link
                    href="/admin/templates"
                    onClick={() => setDropdownOpen(false)}
                    className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                  >
                    <LayoutTemplate className="w-4 h-4 text-primary" />
                    <span>{t("header.form_templates", "Form Templates")}</span>
                  </Link>
                  )}
                  {roleCan("config.manage") && (
                  <Link
                    href="/admin/settings"
                    onClick={() => setDropdownOpen(false)}
                    className="flex items-center gap-2.5 px-4 py-2 text-sm text-textMain hover:bg-white/5 transition-colors font-medium"
                  >
                    <SlidersHorizontal className="w-4 h-4 text-primary" />
                    <span>{t("header.admin_settings", "Settings")}</span>
                  </Link>
                  )}
                </div>
              )}

              <div className="border-t border-borderDark/60 pt-1 mt-1">
                <button
                  onClick={handleLogout}
                  className="w-full flex items-center gap-2.5 px-4 py-2 text-sm text-red-400 hover:bg-red-500/10 transition-colors text-left font-medium"
                >
                  <LogOut className="w-4 h-4" />
                  <span>{t("header.logout", "Log Out")}</span>
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </header>
  );
}

