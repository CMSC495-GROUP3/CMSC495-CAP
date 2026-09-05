import { useEffect, useState, useRef } from 'react'
import { useNavigate, useSearchParams, useLocation } from 'react-router-dom'
import { Menu, MenuButton, MenuItems, MenuItem } from '@headlessui/react'
import {
  Plus, MessageSquare, LayoutGrid, LogOut, Pencil, Trash2,
  Check, X, ChevronRight, FolderOpen, FolderPlus, Folder,
} from 'lucide-react'
import { useConversations } from '../../hooks/useConversations'
import { useProjects } from '../../hooks/useProjects'
import { useAuth } from '../../hooks/useAuth'
import BrandingHeader from './BrandingHeader'
import SidebarToggle from './SidebarToggle'
import type { Conversation, Project } from '../../types'

interface SidebarProps {
  open: boolean
  isDesktop: boolean
  onToggle: () => void
  /** Called after any navigation; the phone layout closes the drawer here. */
  onNavigate?: () => void
}

export default function Sidebar({ open, isDesktop, onToggle, onNavigate }: SidebarProps) {
  const routerNavigate = useNavigate()
  const navigate = (to: string) => {
    routerNavigate(to)
    onNavigate?.()
  }
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const activeSessionId = searchParams.get('session_id')

  const { conversations, fetchConversations, renameConversation, assignToProject, deleteConversation } = useConversations()
  const { projects, fetchProjects, createProject, deleteProject } = useProjects()
  const { logout } = useAuth()

  // Rename state
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)

  // New project inline creation
  const [creatingProject, setCreatingProject] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const newProjectInputRef = useRef<HTMLInputElement>(null)

  // Which project sections are collapsed (all open by default)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  useEffect(() => {
    fetchConversations()
    fetchProjects()
  }, [fetchConversations, fetchProjects, location])

  useEffect(() => {
    if (editingId && editInputRef.current) editInputRef.current.focus()
  }, [editingId])

  useEffect(() => {
    if (creatingProject && newProjectInputRef.current) newProjectInputRef.current.focus()
  }, [creatingProject])

  // ── rename helpers ──────────────────────────────────────────────────────────
  function startEdit(conv: Conversation) {
    setEditingId(conv.session_id)
    setEditValue(conv.title)
  }
  async function commitRename() {
    if (!editingId || !editValue.trim()) return cancelEdit()
    await renameConversation(editingId, editValue.trim())
    setEditingId(null)
  }
  function cancelEdit() { setEditingId(null); setEditValue('') }

  // ── project creation helpers ────────────────────────────────────────────────
  async function commitNewProject() {
    const name = newProjectName.trim()
    if (name) await createProject(name)
    setCreatingProject(false)
    setNewProjectName('')
  }
  function cancelNewProject() { setCreatingProject(false); setNewProjectName('') }

  // ── project collapse ────────────────────────────────────────────────────────
  function toggleCollapse(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  // ── delete conversation ─────────────────────────────────────────────────────
  function handleDeleteConversation(sessionId: string) {
    deleteConversation(sessionId)
    if (activeSessionId === sessionId) navigate('/chat')
  }

  // ── group by project ────────────────────────────────────────────────────────
  const byProject = conversations.reduce<Record<string, Conversation[]>>((acc, conv) => {
    const key = conv.project_id ?? '__ungrouped__'
    if (!acc[key]) acc[key] = []
    acc[key].push(conv)
    return acc
  }, {})

  const ungrouped = byProject['__ungrouped__'] ?? []

  // Shared props for ConversationItem
  const itemProps = {
    activeSessionId,
    editingId,
    editValue,
    editInputRef,
    projects,
    onNavigate: (sid: string) => navigate(`/chat?session_id=${sid}`),
    onStartEdit: startEdit,
    onEditChange: setEditValue,
    onCommitRename: commitRename,
    onCancelEdit: cancelEdit,
    onAssign: assignToProject,
    onDelete: handleDeleteConversation,
  }

  // Desktop: part of the row, simply absent when closed. Phone: a drawer over
  // the page that slides in; it stays mounted so the slide can animate, and
  // `inert` keeps its controls out of the tab order while it is off screen.
  const layoutClass = isDesktop
    ? open ? 'flex' : 'hidden'
    : `fixed inset-y-0 left-0 z-40 flex transform transition-transform duration-200 motion-reduce:transition-none ${
        open ? 'translate-x-0 shadow-2xl shadow-black/60' : '-translate-x-full'
      }`

  return (
    <aside
      id="app-sidebar"
      inert={!isDesktop && !open}
      className={`w-60 flex-shrink-0 flex-col h-screen bg-[#0e1117] border-r border-white/8 ${layoutClass}`}
    >

      {/* Branding */}
      <BrandingHeader />

      {/* New chat, with the collapse control beside it */}
      <div className="px-3 pt-1 pb-2 flex items-center gap-1">
        <button
          onClick={() => navigate('/chat')}
          className="flex-1 flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-gray-300 hover:bg-white/8 hover:text-white transition-colors cursor-pointer"
        >
          <Plus size={16} />
          New chat
        </button>
        <SidebarToggle open={open} onToggle={onToggle} />
      </div>

      {/* Scrollable middle */}
      <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">

        {/* ── Projects section ─────────────────────────────────────────── */}
        <div className="mb-1">
          {/* Section header */}
          <div className="flex items-center justify-between px-3 py-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-600">
              Projects
            </span>
            <button
              onClick={() => setCreatingProject(true)}
              title="New project"
              className="text-gray-600 hover:text-[#C2B067] transition-colors cursor-pointer"
            >
              <FolderPlus size={13} />
            </button>
          </div>

          {/* Inline new-project input */}
          {creatingProject && (
            <div className="flex items-center gap-1 px-3 py-1.5">
              <Folder size={13} className="text-[#C2B067] flex-shrink-0" />
              <input
                ref={newProjectInputRef}
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitNewProject()
                  if (e.key === 'Escape') cancelNewProject()
                }}
                onBlur={commitNewProject}
                placeholder="Project name…"
                className="flex-1 bg-white/8 text-white text-xs rounded px-1.5 py-0.5 outline-none placeholder-gray-600 min-w-0"
              />
              <button onClick={commitNewProject} className="text-[#C2B067] flex-shrink-0"><Check size={12} /></button>
              <button onClick={cancelNewProject} className="text-gray-500 flex-shrink-0"><X size={12} /></button>
            </div>
          )}

          {/* Project list */}
          {projects.length === 0 && !creatingProject && (
            <p className="text-[11px] text-gray-600 px-3 py-1">No projects yet.</p>
          )}

          {projects.map((project) => {
            const projectConvs = byProject[project.project_id] ?? []
            const isCollapsed = collapsed.has(project.project_id)

            return (
              <div key={project.project_id}>
                {/* Project row */}
                <div className="group flex items-center gap-1 px-3 py-1.5 rounded-xl hover:bg-white/5 cursor-pointer"
                  onClick={() => toggleCollapse(project.project_id)}
                >
                  <ChevronRight
                    size={12}
                    className={`flex-shrink-0 text-gray-500 transition-transform duration-150 ${isCollapsed ? '' : 'rotate-90'}`}
                  />
                  <Folder size={13} className="flex-shrink-0 text-[#C2B067] opacity-70" />
                  <span className="flex-1 text-xs text-gray-300 truncate ml-1">{project.name}</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); deleteProject(project.project_id) }}
                    className="opacity-0 group-hover:opacity-100 text-gray-600 hover:text-red-400 transition-all flex-shrink-0"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>

                {/* Conversations under this project */}
                {!isCollapsed && (
                  <div className="ml-4 space-y-0.5">
                    {projectConvs.length === 0 ? (
                      <p className="text-[11px] text-gray-600 px-3 py-1">Empty</p>
                    ) : (
                      projectConvs.map((conv) => (
                        <ConversationItem key={conv.session_id} conv={conv} {...itemProps} />
                      ))
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* ── Ungrouped conversations ───────────────────────────────────── */}
        {(projects.length > 0 || ungrouped.length > 0) && (
          <div>
            {projects.length > 0 && (
              <div className="px-3 py-1.5">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-600">
                  Conversations
                </span>
              </div>
            )}
            {ungrouped.length === 0 && projects.length === 0 && (
              <p className="text-xs text-gray-600 px-3 py-2">No conversations yet.</p>
            )}
            {ungrouped.map((conv) => (
              <ConversationItem key={conv.session_id} conv={conv} {...itemProps} />
            ))}
          </div>
        )}

        {conversations.length === 0 && projects.length === 0 && !creatingProject && (
          <p className="text-xs text-gray-600 px-3 py-2">No conversations yet.</p>
        )}
      </div>

      {/* Bottom nav */}
      <div className="px-2 pb-4 pt-2 border-t border-white/8 space-y-0.5">
        <button
          onClick={() => navigate('/documents')}
          className={`w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm transition-colors cursor-pointer ${
            location.pathname === '/documents'
              ? 'bg-white/10 text-white'
              : 'text-gray-400 hover:bg-white/8 hover:text-white'
          }`}
        >
          <LayoutGrid size={16} />
          Policy Library
        </button>
        <button
          onClick={logout}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-gray-400 hover:bg-white/8 hover:text-white transition-colors cursor-pointer"
        >
          <LogOut size={16} />
          Sign out
        </button>
      </div>
    </aside>
  )
}

// ─── ConversationItem ─────────────────────────────────────────────────────────

interface ConversationItemProps {
  conv: Conversation
  activeSessionId: string | null
  editingId: string | null
  editValue: string
  editInputRef: React.RefObject<HTMLInputElement | null>
  projects: Project[]
  onNavigate: (sid: string) => void
  onStartEdit: (conv: Conversation) => void
  onEditChange: (v: string) => void
  onCommitRename: () => void
  onCancelEdit: () => void
  onAssign: (sessionId: string, projectId: string | null) => void
  onDelete: (sessionId: string) => void
}

function ConversationItem({
  conv, activeSessionId, editingId, editValue, editInputRef,
  projects, onNavigate, onStartEdit, onEditChange,
  onCommitRename, onCancelEdit, onAssign, onDelete,
}: ConversationItemProps) {
  const [hovered, setHovered] = useState(false)
  const isActive = conv.session_id === activeSessionId
  const isEditing = editingId === conv.session_id

  return (
    <div
      className={`group relative flex items-center rounded-xl px-3 py-2 transition-colors ${
        isActive ? 'bg-white/10 text-white' : 'text-gray-400 hover:bg-white/6 hover:text-gray-200'
      }`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <MessageSquare size={13} className="flex-shrink-0 mr-2 opacity-50" />

      {isEditing ? (
        <div className="flex-1 flex items-center gap-1">
          <input
            ref={editInputRef}
            value={editValue}
            onChange={(e) => onEditChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onCommitRename()
              if (e.key === 'Escape') onCancelEdit()
            }}
            onBlur={onCommitRename}
            className="flex-1 bg-white/10 text-white text-xs rounded px-1.5 py-0.5 outline-none min-w-0"
          />
          <button onClick={onCommitRename} className="text-[#C2B067] flex-shrink-0"><Check size={12} /></button>
          <button onClick={onCancelEdit} className="text-gray-400 flex-shrink-0"><X size={12} /></button>
        </div>
      ) : (
        <>
          <span onClick={() => onNavigate(conv.session_id)} className="flex-1 text-xs truncate cursor-pointer">
            {conv.title}
          </span>

          {hovered && (
            <div className="flex items-center gap-1 ml-1 flex-shrink-0">
              {/* Rename */}
              <button
                onClick={(e) => { e.stopPropagation(); onStartEdit(conv) }}
                className="text-gray-500 hover:text-gray-200 transition-colors"
              >
                <Pencil size={11} />
              </button>

              {/* Move to project (only if projects exist) */}
              {projects.length > 0 && (
                <Menu as="div" className="relative">
                  <MenuButton
                    onClick={(e) => e.stopPropagation()}
                    className="text-gray-500 hover:text-[#C2B067] transition-colors cursor-pointer"
                    title="Move to project"
                  >
                    <FolderOpen size={11} />
                  </MenuButton>
                  <MenuItems
                    anchor="bottom end"
                    className="z-50 w-44 rounded-xl border border-white/10 bg-[#1a1a1a] shadow-xl text-xs py-1 focus:outline-none"
                  >
                    {/* Unassign option (only show if currently in a project) */}
                    {conv.project_id && (
                      <MenuItem>
                        <button
                          onClick={() => onAssign(conv.session_id, null)}
                          className="w-full text-left px-3 py-2 text-gray-400 hover:bg-white/8 hover:text-white transition-colors data-[focus]:bg-white/8"
                        >
                          Remove from project
                        </button>
                      </MenuItem>
                    )}
                    {projects.map((p) => (
                      <MenuItem key={p.project_id}>
                        <button
                          onClick={() => onAssign(conv.session_id, p.project_id)}
                          className={`w-full text-left px-3 py-2 transition-colors data-[focus]:bg-white/8 ${
                            conv.project_id === p.project_id
                              ? 'text-[#C2B067]'
                              : 'text-gray-300 hover:bg-white/8 hover:text-white'
                          }`}
                        >
                          {conv.project_id === p.project_id ? '✓ ' : ''}{p.name}
                        </button>
                      </MenuItem>
                    ))}
                  </MenuItems>
                </Menu>
              )}

              {/* Delete */}
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(conv.session_id) }}
                className="text-gray-500 hover:text-red-400 transition-colors"
              >
                <Trash2 size={11} />
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
