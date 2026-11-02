/**
 * cn — the single class-merge helper (shadcn/ui convention).
 *
 * Every primitive funnels className through here so a caller's class always
 * wins over a variant default without a specificity war.
 */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
