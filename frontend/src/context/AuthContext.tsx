import React, { createContext, useContext, useState, useEffect } from 'react';
import { User, UserRole } from '../types';
import { apiService } from '../services/apiService';

interface AuthContextType {
  user: User | null;
  role: UserRole;
  isAuthenticated: boolean;
  isLoading: boolean;     // initial session check only
  isSigningIn: boolean;   // a sign-in request is in flight
  login: (credentials: { username: string; password?: string }) => Promise<void>;
  logout: () => Promise<void>;
  demoLogin: (role: UserRole) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSigningIn, setIsSigningIn] = useState(false);

  useEffect(() => {
    async function checkAuth() {
      try {
        const currentUser = await apiService.getMe();
        if (currentUser) {
          setUser(currentUser);
        }
      } catch (err) {
        console.warn('Auth check error', err);
      } finally {
        setIsLoading(false);
      }
    }
    checkAuth();
  }, []);

  const login = async (credentials: { username: string; password?: string }) => {
    setIsSigningIn(true);
    try {
      const loggedUser = await apiService.login({
        username: credentials.username,
        password: credentials.password || 'password123',
      });
      setUser(loggedUser);
    } finally {
      setIsSigningIn(false);
    }
  };

  const demoLogin = async (role: UserRole) => {
    setIsSigningIn(true);
    try {
      const loggedUser = await apiService.login({
        username: role === 'admin' ? 'admin' : 'viewer',
        password: role === 'admin' ? 'admin123' : 'viewer123',
      });
      setUser(loggedUser);
    } finally {
      setIsSigningIn(false);
    }
  };

  const logout = async () => {
    await apiService.logout();
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        role: user?.role || 'viewer',
        isAuthenticated: !!user,
        isLoading,
        isSigningIn,
        login,
        logout,
        demoLogin,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
