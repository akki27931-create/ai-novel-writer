/** 路由表。 */
import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Toaster from './components/Toast'
import BookDetail from './pages/BookDetail'
import Home from './pages/Home'
import Settings from './pages/Settings'
import Usage from './pages/Usage'
import Write from './pages/Write'

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="books/:bookId" element={<BookDetail />} />
          <Route path="books/:bookId/write" element={<Write />} />
          <Route path="settings" element={<Settings />} />
          <Route path="usage" element={<Usage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <Toaster />
    </>
  )
}
