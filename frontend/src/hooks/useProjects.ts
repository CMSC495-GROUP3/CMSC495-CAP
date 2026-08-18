import { useState, useCallback } from 'react'
import client from '../api/client'
import type { Project } from '../types'

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([])

  const fetchProjects = useCallback(async () => {
    const res = await client.get<Project[]>('/api/projects')
    setProjects(res.data)
  }, [])

  async function createProject(name: string): Promise<Project> {
    const res = await client.post<Project>('/api/projects', { name })
    setProjects((prev) => [...prev, res.data])
    return res.data
  }

  async function deleteProject(projectId: string) {
    await client.delete(`/api/projects/${projectId}`)
    setProjects((prev) => prev.filter((p) => p.project_id !== projectId))
  }

  return { projects, fetchProjects, createProject, deleteProject }
}
