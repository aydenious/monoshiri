"use client";

import {
	Check,
	ChevronRight,
	ChevronUp,
	Languages,
	LogOut,
	Monitor,
	Moon,
	Sun,
	UserCog,
} from "lucide-react";
import Image from "next/image";
import { useTranslations } from "next-intl";
import type React from "react";
import { Fragment, useState } from "react";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerContent, DrawerHandle, DrawerTitle } from "@/components/ui/drawer";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuPortal,
	DropdownMenuSeparator,
	DropdownMenuSub,
	DropdownMenuSubContent,
	DropdownMenuSubTrigger,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Spinner } from "@/components/ui/spinner";
import { useLocaleContext } from "@/contexts/LocaleContext";
import { useMediaQuery } from "@/hooks/use-media-query";
import { LANGUAGES } from "@/lib/languages";
import { getUserAvatarColor, getUserInitials } from "@/lib/user-avatar";
import { cn } from "@/lib/utils";
import type { User } from "../../types/layout.types";

// Supported themes configuration
const THEMES = [
	{ value: "light" as const, name: "Light", icon: Sun },
	{ value: "dark" as const, name: "Dark", icon: Moon },
	{ value: "system" as const, name: "System", icon: Monitor },
];

type MobileProfileSubmenu = "theme" | "language";

interface SidebarUserProfileProps {
	user: User;
	onUserSettings?: () => void;
	onLogout?: () => void;
	isCollapsed?: boolean;
	theme?: string;
	setTheme?: (theme: "light" | "dark" | "system") => void;
	topContent?: React.ReactNode;
}

/**
 * User avatar component - shows image if available, otherwise falls back to initials
 */
function UserAvatar({
	avatarUrl,
	initials,
	bgColor,
	size = "sm",
}: {
	avatarUrl?: string;
	initials: string;
	bgColor: string;
	size?: "sm" | "md";
}) {
	const sizeClass = size === "md" ? "h-10 w-10" : "h-8 w-8";

	if (avatarUrl) {
		return (
			<Image
				src={avatarUrl}
				alt="User avatar"
				width={size === "md" ? 40 : 32}
				height={size === "md" ? 40 : 32}
				className={cn(sizeClass, "shrink-0 rounded-full object-cover select-none")}
				referrerPolicy="no-referrer"
				unoptimized
			/>
		);
	}

	return (
		<div
			className={cn(
				sizeClass,
				"flex shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white select-none"
			)}
			style={{ backgroundColor: bgColor }}
		>
			{initials}
		</div>
	);
}

export function SidebarUserProfile({
	user,
	onUserSettings,
	onLogout,
	isCollapsed = false,
	theme,
	setTheme,
	topContent,
}: SidebarUserProfileProps) {
	const t = useTranslations("sidebar");
	const { locale, setLocale } = useLocaleContext();
	const isDesktopViewport = useMediaQuery("(min-width: 768px)");
	const [isLoggingOut, setIsLoggingOut] = useState(false);
	const [mobileSubmenu, setMobileSubmenu] = useState<MobileProfileSubmenu | null>(null);
	const bgColor = getUserAvatarColor(user.email);
	const initials = getUserInitials(user.email);
	const displayName = user.name || user.email.split("@")[0];
	const useMobileSubmenus = !isDesktopViewport;

	const handleLanguageChange = (newLocale: "en" | "es" | "pt" | "hi" | "zh" | "ko") => {
		setLocale(newLocale);
	};

	const handleThemeChange = (newTheme: "light" | "dark" | "system") => {
		setTheme?.(newTheme);
	};

	const handleLogout = async () => {
		if (isLoggingOut || !onLogout) return;
		setIsLoggingOut(true);
		try {
			await onLogout();
		} finally {
			setIsLoggingOut(false);
		}
	};

	const submenuTriggerClassName = "cursor-default";
	const drawerItemClassName = cn(
		"flex h-12 w-full items-center gap-3 rounded-lg px-3 text-left text-sm transition-colors",
		"hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
	);
	const drawerSeparatorClassName = "mx-3 h-px bg-popover-border";

	const renderThemeSubmenu = () => {
		if (!setTheme) return null;

		if (useMobileSubmenus) {
			return (
				<DropdownMenuItem
					className={submenuTriggerClassName}
					onSelect={() => setMobileSubmenu("theme")}
				>
					<Sun className="h-4 w-4" />
					<span className="flex-1">{t("theme")}</span>
					<ChevronRight className="h-4 w-4 text-muted-foreground" />
				</DropdownMenuItem>
			);
		}

		return (
			<DropdownMenuSub>
				<DropdownMenuSubTrigger>
					<Sun className="h-4 w-4" />
					{t("theme")}
				</DropdownMenuSubTrigger>
				<DropdownMenuPortal>
					<DropdownMenuSubContent className="gap-1">
						{THEMES.map((themeOption) => {
							const Icon = themeOption.icon;
							const isSelected = theme === themeOption.value;
							return (
								<DropdownMenuItem
									key={themeOption.value}
									onClick={() => handleThemeChange(themeOption.value)}
									className={cn(
										"mb-1 last:mb-0 transition-all",
										"hover:bg-accent hover:text-accent-foreground",
										isSelected && "text-primary"
									)}
								>
									<Icon className="h-4 w-4" />
									<span className="flex-1">{t(themeOption.value)}</span>
									{isSelected && <Check className="h-4 w-4 shrink-0" />}
								</DropdownMenuItem>
							);
						})}
					</DropdownMenuSubContent>
				</DropdownMenuPortal>
			</DropdownMenuSub>
		);
	};

	const renderLanguageSubmenu = () => {
		if (useMobileSubmenus) {
			return (
				<DropdownMenuItem
					className={submenuTriggerClassName}
					onSelect={() => setMobileSubmenu("language")}
				>
					<Languages className="h-4 w-4" />
					<span className="flex-1">{t("language")}</span>
					<ChevronRight className="h-4 w-4 text-muted-foreground" />
				</DropdownMenuItem>
			);
		}

		return (
			<DropdownMenuSub>
				<DropdownMenuSubTrigger>
					<Languages className="h-4 w-4" />
					{t("language")}
				</DropdownMenuSubTrigger>
				<DropdownMenuPortal>
					<DropdownMenuSubContent className="gap-1">
						{LANGUAGES.map((language) => {
							const isSelected = locale === language.code;
							return (
								<DropdownMenuItem
									key={language.code}
									onClick={() => handleLanguageChange(language.code)}
									className={cn(
										"mb-1 last:mb-0 transition-all",
										"hover:bg-accent hover:text-accent-foreground",
										isSelected && "text-primary"
									)}
								>
									<span className="mr-2">{language.flag}</span>
									<span className="flex-1">{language.name}</span>
									{isSelected && <Check className="h-4 w-4 shrink-0" />}
								</DropdownMenuItem>
							);
						})}
					</DropdownMenuSubContent>
				</DropdownMenuPortal>
			</DropdownMenuSub>
		);
	};

	const mobileSubmenuDrawer = (
		<Drawer
			open={mobileSubmenu !== null}
			onOpenChange={(open) => {
				if (!open) setMobileSubmenu(null);
			}}
			shouldScaleBackground={false}
		>
			<DrawerContent
				className="z-80 max-h-[75vh] rounded-t-2xl border bg-popover text-popover-foreground"
				overlayClassName="z-80"
			>
				<DrawerHandle className="mt-3 h-1.5 w-10" />
				<DrawerTitle className="px-4 pb-2 pt-3 text-center text-base font-semibold">
					{mobileSubmenu === "theme" ? t("theme") : t("language")}
				</DrawerTitle>
				<div className="space-y-1 px-4 pb-6 pt-1">
					{mobileSubmenu === "theme" &&
						setTheme &&
						THEMES.map((themeOption, index) => {
							const Icon = themeOption.icon;
							const isSelected = theme === themeOption.value;
							return (
								<Fragment key={themeOption.value}>
									{index > 0 && <div className={drawerSeparatorClassName} />}
									<button
										type="button"
										className={cn(drawerItemClassName, isSelected && "text-primary")}
										onClick={() => {
											handleThemeChange(themeOption.value);
											setMobileSubmenu(null);
										}}
									>
										<Icon className="h-4 w-4 text-muted-foreground" />
										<span className="flex-1">{t(themeOption.value)}</span>
										{isSelected && <Check className="h-4 w-4 shrink-0" />}
									</button>
								</Fragment>
							);
						})}

					{mobileSubmenu === "language" &&
						LANGUAGES.map((language, index) => {
							const isSelected = locale === language.code;
							return (
								<Fragment key={language.code}>
									{index > 0 && <div className={drawerSeparatorClassName} />}
									<button
										type="button"
										className={cn(drawerItemClassName, isSelected && "text-primary")}
										onClick={() => {
											handleLanguageChange(language.code);
											setMobileSubmenu(null);
										}}
									>
										<span className="text-base">{language.flag}</span>
										<span className="flex-1">{language.name}</span>
										{isSelected && <Check className="h-4 w-4 shrink-0" />}
									</button>
								</Fragment>
							);
						})}
				</div>
			</DrawerContent>
		</Drawer>
	);

	// Collapsed view - just show avatar with dropdown
	if (isCollapsed) {
		return (
			<div className="relative w-full px-1.5 py-2 before:absolute before:inset-x-1.5 before:top-0 before:h-px before:bg-border">
				<div className="flex flex-col items-center gap-2">
					{topContent}
					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<Button
								type="button"
								variant="ghost"
								className={cn(
									"h-10 w-10 rounded-full p-0",
									"transition-opacity hover:bg-transparent hover:opacity-90",
									"focus:outline-none focus-visible:outline-none",
									"data-[state=open]:opacity-90"
								)}
							>
								<UserAvatar
									avatarUrl={user.avatarUrl}
									initials={initials}
									bgColor={bgColor}
									size="md"
								/>
								<span className="sr-only">{displayName}</span>
							</Button>
						</DropdownMenuTrigger>

						<DropdownMenuContent className="w-48" side="right" align="end" sideOffset={8}>
							<DropdownMenuLabel className="px-2 py-1 font-normal">
								<div className="min-w-0">
									{/* <p className="truncate text-sm font-medium">{displayName}</p> */}
									<p className="truncate text-xs font-semibold leading-tight text-muted-foreground">
										{user.email}
									</p>
								</div>
							</DropdownMenuLabel>

							<DropdownMenuItem onClick={onUserSettings}>
								<UserCog className="h-4 w-4" />
								{t("user_settings")}
							</DropdownMenuItem>

							{renderThemeSubmenu()}

							{renderLanguageSubmenu()}

							<DropdownMenuSeparator />

							<DropdownMenuItem onClick={handleLogout} disabled={isLoggingOut}>
								{isLoggingOut ? (
									<Spinner size="sm" className="mr-2" />
								) : (
									<LogOut className="h-4 w-4" />
								)}
								{isLoggingOut ? t("loggingOut") : t("logout")}
							</DropdownMenuItem>
						</DropdownMenuContent>
					</DropdownMenu>
					{mobileSubmenuDrawer}
				</div>
			</div>
		);
	}

	// Expanded view
	return (
		<div className="border-t">
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button
						type="button"
						variant="ghost"
						className={cn(
							"h-auto w-full justify-start gap-2 rounded-none px-2 py-3 text-left",
							"hover:bg-accent transition-colors",
							"focus:outline-none focus-visible:outline-none",
							"data-[state=open]:bg-transparent"
						)}
					>
						<UserAvatar avatarUrl={user.avatarUrl} initials={initials} bgColor={bgColor} />

						{/* Name and email */}
						<div className="flex-1 min-w-0">
							<p className="truncate text-sm font-medium">{displayName}</p>
							<p className="truncate text-xs text-muted-foreground">{user.email}</p>
						</div>

						{/* Chevron icon */}
						<ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
					</Button>
				</DropdownMenuTrigger>

				<DropdownMenuContent className="w-48" side="top" align="center" sideOffset={4}>
					<DropdownMenuLabel className="px-2 py-1 font-normal">
						<div className="min-w-0">
							<p className="truncate text-sm font-medium">{displayName}</p>
							<p className="truncate text-xs font-semibold leading-tight text-muted-foreground">
								{user.email}
							</p>
						</div>
					</DropdownMenuLabel>

					<DropdownMenuItem onClick={onUserSettings}>
						<UserCog className="h-4 w-4" />
						{t("user_settings")}
					</DropdownMenuItem>

					{renderThemeSubmenu()}

					{renderLanguageSubmenu()}

					<DropdownMenuSeparator />

					<DropdownMenuItem onClick={handleLogout} disabled={isLoggingOut}>
						{isLoggingOut ? <Spinner size="sm" className="mr-2" /> : <LogOut className="h-4 w-4" />}
						{isLoggingOut ? t("loggingOut") : t("logout")}
					</DropdownMenuItem>
				</DropdownMenuContent>
			</DropdownMenu>
			{mobileSubmenuDrawer}
		</div>
	);
}
