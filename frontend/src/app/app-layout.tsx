import { LogOut, Monitor, Moon, Sun } from 'lucide-react'
import { useTheme } from '@/lib/theme'
import { Link, Navigate, Outlet, useLocation } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Separator } from '@/components/ui/separator'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar'
import { Spinner } from '@/components/spinner'
import { NAV, titleFor } from '@/app/nav'
import { useAuth } from '@/features/auth/auth-context'

/** Every route except /login: requires a two-factor session (ARCHITECTURE.md S8). */
export function AppLayout() {
  const auth = useAuth()
  const location = useLocation()
  if (auth.status === 'loading') return <Spinner label="Checking your session" className="min-h-svh" />
  if (auth.status !== 'signed-in') return <Navigate to="/login" replace state={{ from: location.pathname }} />

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-2 border-b bg-background/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
          <SidebarTrigger aria-label="Toggle menu" />
          <Separator orientation="vertical" className="mr-1 data-vertical:h-4 data-vertical:self-center" />
          <h1 className="truncate font-heading text-sm font-semibold">{titleFor(location.pathname)}</h1>
          <div className="ml-auto flex items-center gap-1">
            {auth.bypass && <Badge variant="outline">local dev</Badge>}
            <ThemeMenu />
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-5 md:px-6">
          <div key={location.pathname} className="animate-in fade-in-0 slide-in-from-bottom-1 duration-200 ease-out">
            <Outlet />
          </div>
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}

function AppSidebar() {
  const { pathname } = useLocation()
  const { setOpenMobile } = useSidebar()
  const auth = useAuth()
  const active = (to: string) => (to === '/' ? pathname === '/' : pathname === to || pathname.startsWith(`${to}/`))

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link to="/" onClick={() => setOpenMobile(false)} />}>
              <div className="grid size-8 shrink-0 place-items-center bg-primary text-primary-foreground">
                <span className="font-heading text-sm font-bold">B</span>
              </div>
              <div className="grid leading-tight">
                <span className="font-heading text-sm font-semibold tracking-tight">BETTORY</span>
                <span className="text-xs text-muted-foreground">15-minute verification</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        {NAV.map((g) => (
          <SidebarGroup key={g.group}>
            <SidebarGroupLabel>{g.group}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {g.items.map((item) => (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton
                      isActive={active(item.to)}
                      tooltip={item.label}
                      render={<Link to={item.to} onClick={() => setOpenMobile(false)} />}
                    >
                      <item.icon />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              tooltip="Sign out"
              onClick={() => void auth.signOut()}
              disabled={auth.bypass}
              title={auth.bypass ? 'Local development: no login session' : undefined}
            >
              <LogOut />
              <span className="truncate">{auth.bypass ? 'Local development' : `Sign out · ${auth.email ?? ''}`}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function ThemeMenu() {
  const { setTheme, resolvedTheme } = useTheme()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Theme" />}>
        {resolvedTheme === 'dark' ? <Moon /> : <Sun />}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => setTheme('light')}>
          <Sun /> Light
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme('dark')}>
          <Moon /> Dark
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme('system')}>
          <Monitor /> System
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
