import { useEffect, useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { browserStyles } from "./styles/browser_style";

function FolderBrowser() {
  const [path, setPath] = useState("");
  const [folders, setFolders] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const backendUrl = import.meta.env.VITE_BACKEND_URL;
  const navigate = useNavigate();

  useEffect(() => {
    fetch(`${backendUrl}/api/records/list?subpath=${encodeURIComponent(path)}`)
      .then((res) => res.json())
      .then((data) => {
        setFolders(data.folders || []);
        setSelected(new Set()); 
      })
      .catch((err) => console.error("Fetch error:", err));
  }, [path, backendUrl]);

  const groupedFolders = useMemo(() => {
    const getGroupName = (name) => {
      try {
        const year = name.substring(0, 4);
        const month = name.substring(4, 6);
        const day = name.substring(6, 8);
        const folderDate = new Date(`${year}-${month}-${day}T00:00:00`);
        
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        const lastWeek = new Date(today);
        lastWeek.setDate(lastWeek.getDate() - 7);

        if (isNaN(folderDate.getTime())) return "Older";
        if (folderDate >= today) return "Today";
        if (folderDate >= yesterday) return "Yesterday";
        if (folderDate >= lastWeek) return "Last Week";
        return "Older";
      } catch {
        return "Older";
      }
    };

    const groups = { Today: [], Yesterday: [], "Last Week": [], Older: [] };
    folders.forEach(f => groups[getGroupName(f.name)].push(f));
    return groups;
  }, [folders]);

  const toggleSelect = (name) => {
    const fullPath = path ? `${path}/${name}` : name;
    const newSet = new Set(selected);
    if (newSet.has(fullPath)) newSet.delete(fullPath);
    else newSet.add(fullPath);
    setSelected(newSet);
  };

  const toggleGroup = (groupItems) => {
    const newSet = new Set(selected);
    const itemPaths = groupItems.map(f => path ? `${path}/${f.name}` : f.name);
    const allInGroupSelected = itemPaths.every(p => selected.has(p));

    itemPaths.forEach(p => {
      if (allInGroupSelected) newSet.delete(p);
      else newSet.add(p);
    });
    setSelected(newSet);
  };

  const downloadSelected = () => {
    if (selected.size === 0) return;
    const params = new URLSearchParams();
    selected.forEach(p => params.append("paths", p));
    window.location.href = `${backendUrl}/api/records/download-multiple?${params.toString()}`;
  };

  return (
    <div style={browserStyles.page}>
      {/* Header Bar */}
      <header style={browserStyles.header}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <button onClick={() => navigate("/")} style={browserStyles.backBtn}>
            ← Back
          </button>
          <div style={browserStyles.pathBar}>
            <span style={{ color: "#555" }}>Path:</span> 
            <span style={{ color: "#fff"}}>/{path}</span>
            {path && (
              <button 
                onClick={() => setPath(path.split("/").slice(0, -1).join("/"))} 
                style={browserStyles.actionBtn}
              >
                Up
              </button>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: "12px" }}>
          {selected.size > 0 && (
            <button onClick={() => setSelected(new Set())} style={{ ...browserStyles.actionBtn, background: "transparent" }}>
              Clear Selection
            </button>
          )}
          <button 
            onClick={downloadSelected} 
            disabled={selected.size === 0}
            style={browserStyles.downloadBtn(selected.size > 0)}
          >
            Download ({selected.size})
          </button>
        </div>
      </header>

      {/* Table Area */}
      <div style={browserStyles.scrollContainer}>
        <div style={{ maxWidth: "1200px", margin: "0 auto" }}>
          
          {/* Table Headings */}
          <div style={browserStyles.tableHeader}>
            <div style={{ width: "40px" }}></div>
            <div style={{ flex: "2" }}>Test</div>
            <div style={{ flex: "1" }}>Result</div>
            <div style={{ width: "150px", textAlign: "right" }}>Actions</div>
          </div>

          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {Object.entries(groupedFolders).map(([groupName, items]) => {
              if (items.length === 0) return null;

              const groupPaths = items.map(f => path ? `${path}/${f.name}` : f.name);
              const isGroupSelected = groupPaths.every(p => selected.has(p));

              return (
                <div key={groupName}>
                  <div style={browserStyles.categoryHeader}>
                    <input 
                      type="checkbox" 
                      checked={isGroupSelected} 
                      onChange={() => toggleGroup(items)}
                      style={{ width: "16px", height: "16px", accentColor: "#2f80ed" }}
                    />
                    <span style={{ marginLeft: "12px" }}>
                      {groupName.toUpperCase()} <span style={{ opacity: 0.5, fontWeight: "400" }}>— {items.length} folders</span>
                    </span>
                  </div>

                  {items.map((folder) => {
                    const fullPath = path ? `${path}/${folder.name}` : folder.name;
                    const isChecked = selected.has(fullPath);

                    return (
                      <li key={folder.name} style={browserStyles.row(isChecked)}>
                        <div style={{ width: "40px" }}>
                          <input 
                            type="checkbox" 
                            checked={isChecked} 
                            onChange={() => toggleSelect(folder.name)}
                            style={{ width: "16px", height: "16px", accentColor: "#2f80ed" }}
                          />
                        </div>
                        
                        <div style={{ flex: "2", display: "flex", alignItems: "center", gap: "10px" }}>
                          <span style={{ opacity: 0.7 }}>📁</span> 
                          <span style={{ fontWeight: isChecked ? "700" : "400" }}>{folder.name}</span>
                        </div>
                        
                        <div style={{ flex: "1", color: "#666", fontSize: "13px" }}>
                          {folder.result || "--"}
                        </div>

                        <div style={{ width: "210px", display: "flex", gap: "8px", justifyContent: "flex-end" }}>
  <button onClick={() => setPath(fullPath)} style={browserStyles.actionBtn}>Open</button>
  <a href={`${backendUrl}/api/records/download-folder?path=${encodeURIComponent(fullPath)}`}>
    <button style={browserStyles.actionBtn}>Zip</button>
  </a>
  <a href={`${backendUrl}/api/records/report?path=${encodeURIComponent(fullPath)}`}
     target="_blank" rel="noopener noreferrer">
    <button style={browserStyles.actionBtn}>Report</button>
  </a>
</div>
                      </li>
                    );
                  })}
                </div>
              );
            })}
          </ul>
        </div>
      </div>

      <footer style={browserStyles.footer}>
        <span style={{ color: "#2f80ed", fontWeight: "800" }}>ACM</span>
        <span style={{ margin: "0 8px", color: "#ffffff" }}>|</span>
        <span style={{ color: "#ffffff" }}>
          Powered by <strong style={{ color: "#ffcc00", fontWeight: "700" }}>MTU</strong>
        </span>
        <span style={{ marginLeft: "8px", color: "#ffffff" }}>© 2026</span>
      </footer>
    </div>
  );
}

export default FolderBrowser;