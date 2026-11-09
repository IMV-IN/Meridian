import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * autoFocus only where it helps: a fine pointer (desktop). On touch devices it
 * pops the keyboard and scrolls the page before the person has read it.
 */
export function autoFocusDesktop(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(pointer: fine)").matches
}
