-- Enable Row Level Security
ALTER TABLE graphs ENABLE ROW LEVEL SECURITY;
ALTER TABLE nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE exports ENABLE ROW LEVEL SECURITY;

-- Create policies (allow all for now, adjust based on your auth requirements)
CREATE POLICY "Allow all operations on graphs" ON graphs FOR ALL USING (true);
CREATE POLICY "Allow all operations on nodes" ON nodes FOR ALL USING (true);
CREATE POLICY "Allow all operations on edges" ON edges FOR ALL USING (true);
CREATE POLICY "Allow all operations on documents" ON documents FOR ALL USING (true);
CREATE POLICY "Allow all operations on exports" ON exports FOR ALL USING (true);
