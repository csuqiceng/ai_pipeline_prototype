import React, { useState } from 'react';
import { motion } from 'motion/react';
import { Settings, BadgeCheck, ShieldAlert, LogIn as LoginIcon, Cpu, Network } from 'lucide-react';

export default function Login({ onLogin }: { onLogin: (role: 'ENGINEER' | 'OPERATOR') => void }) {
  const [role, setRole] = useState<'ENGINEER' | 'OPERATOR'>('OPERATOR');
  const [operatorId, setOperatorId] = useState('');
  const [pin, setPin] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onLogin(role);
  };

  return (
    <div className="min-h-screen dot-grid flex flex-col items-center justify-center relative px-6">
      {/* Top Accent Bar */}
      <div className="absolute top-0 left-0 w-full h-1 bg-primary-container z-50"></div>
      
      {/* Background Blurs */}
      <div className="absolute -top-40 -left-40 w-96 h-96 bg-primary-container opacity-5 rounded-full blur-3xl pointer-events-none"></div>
      <div className="absolute -bottom-40 -right-40 w-96 h-96 bg-secondary opacity-5 rounded-full blur-3xl pointer-events-none"></div>

      <motion.main 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-[440px] z-10"
      >
        <div className="glass-panel rounded-xl shadow-xl p-8 flex flex-col gap-8 w-full relative overflow-hidden">
          {/* Header */}
          <header className="flex flex-col items-center gap-2">
            <div className="w-14 h-14 bg-white rounded-full flex items-center justify-center mb-2 border border-border-divider/50 shadow-sm">
              <Cpu className="text-primary-container" size={32} />
            </div>
            <h1 className="text-2xl font-black text-primary-container tracking-tight text-center">KINETIX 控制系统</h1>
            <p className="text-[10px] text-text-secondary tracking-[0.2em] font-bold uppercase">安全访问门户</p>
          </header>

          <form className="flex flex-col gap-6" onSubmit={handleSubmit}>
            {/* Role Selection */}
            <div className="flex flex-col gap-2">
              <label className="text-[10px] font-bold text-text-secondary uppercase tracking-wider" htmlFor="role">
                访问权限级别
              </label>
              <select 
                className="w-full appearance-none bg-surface border border-border-divider rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-secondary focus:border-secondary transition-all cursor-pointer"
                id="role"
                value={role}
                onChange={(e) => setRole(e.target.value as 'ENGINEER' | 'OPERATOR')}
              >
                <option value="OPERATOR">普通用户 (操作员)</option>
                <option value="ENGINEER">工程师 (后台管理)</option>
              </select>
            </div>

            {/* Operator ID */}
            <div className="flex flex-col gap-2">
              <label className="text-[10px] font-bold text-text-secondary uppercase tracking-wider" htmlFor="operatorId">
                操作员 ID
              </label>
              <div className="relative flex items-center">
                <BadgeCheck className="absolute left-4 text-status-disabled" size={20} />
                <input 
                  className="w-full bg-surface border border-border-divider rounded-lg font-mono text-sm pl-12 pr-4 py-3 focus:outline-none focus:ring-2 focus:ring-secondary focus:border-secondary transition-all"
                  id="operatorId"
                  placeholder="ID-8888"
                  required
                  value={operatorId}
                  onChange={(e) => setOperatorId(e.target.value)}
                  type="text"
                />
              </div>
            </div>

            {/* Security PIN */}
            <div className="flex flex-col gap-2">
              <label className="text-[10px] font-bold text-text-secondary uppercase tracking-wider" htmlFor="pin">
                安全 PIN 码
              </label>
              <div className="relative flex items-center">
                <ShieldAlert className="absolute left-4 text-status-disabled" size={20} />
                <input 
                  className="w-full bg-surface border border-border-divider rounded-lg font-mono text-sm pl-12 pr-4 py-3 focus:outline-none focus:ring-2 focus:ring-secondary focus:border-secondary transition-all tracking-[0.5em]"
                  id="pin"
                  placeholder="••••••••"
                  required
                  value={pin}
                  onChange={(e) => setPin(e.target.value)}
                  type="password"
                />
              </div>
            </div>

            <button 
              className="w-full bg-primary-container text-white font-black py-4 rounded-lg hover:brightness-110 active:scale-[0.98] transition-all flex items-center justify-center gap-2 mt-2 shadow-lg shadow-primary-container/20"
              type="submit"
            >
              <span>初始化会话</span>
            </button>
          </form>

          <div className="text-center mt-2">
            <a className="text-[11px] font-medium text-secondary hover:text-primary transition-colors" href="#">
              紧急访问覆盖 (Emergency Override)
            </a>
          </div>
        </div>
      </motion.main>

      {/* Global Status Bar */}
      <footer className="fixed bottom-0 left-0 w-full h-12 bg-white border-t border-border-divider flex items-center justify-between px-6 z-50">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-primary-container animate-pulse"></div>
            <span className="font-mono text-[10px] text-text-secondary">服务器状态: 在线</span>
          </div>
          <div className="h-4 w-[1px] bg-border-divider"></div>
          <div className="flex items-center gap-2 text-text-secondary">
            <Network size={14} />
            <span className="font-mono text-[10px]">网络延迟: 8ms</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-status-disabled">版本: V3.4.1-STABLE</span>
        </div>
      </footer>
    </div>
  );
}
