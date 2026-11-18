import {
  Activity,
  BadgeCheck,
  Bell,
  CalendarDays,
  ClipboardList,
  FlaskConical,
  HeartPulse,
  House,
  LineChart,
  Settings,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
}

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: 'Betting',
    items: [
      { to: '/', label: 'Today', icon: House },
      { to: '/sessions', label: 'Bets section', icon: BadgeCheck },
      { to: '/bets', label: 'My bets', icon: ClipboardList },
      { to: '/performance', label: 'Performance', icon: LineChart },
    ],
  },
  {
    group: 'Forward test',
    items: [
      { to: '/paper', label: 'Paper betting', icon: FlaskConical },
      { to: '/recorder', label: 'Recorder', icon: Activity },
    ],
  },
  {
    group: 'Data',
    items: [
      { to: '/calendar', label: 'Calendar', icon: CalendarDays },
      { to: '/data-health', label: 'Data health', icon: HeartPulse },
    ],
  },
  {
    group: 'System',
    items: [
      { to: '/notifications', label: 'Notifications', icon: Bell },
      { to: '/settings', label: 'Settings', icon: Settings },
    ],
  },
]

export function titleFor(pathname: string): string {
  if (pathname.startsWith('/matches/')) return 'Match analysis'
  const all = NAV.flatMap((g) => g.items)
  const hit = all
    .filter((i) => (i.to === '/' ? pathname === '/' : pathname === i.to || pathname.startsWith(`${i.to}/`)))
    .sort((a, b) => b.to.length - a.to.length)[0]
  return hit?.label ?? 'BETTORY'
}
