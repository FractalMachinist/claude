# claude_high_level_library with Numba acceleration
# claude_top_level_library

import claude_low_level_library_numba as low_level
import numpy as np
from numba import jit, njit, prange
# cimport numpy as np
# cimport cython

# ctypedef np.float64_t DTYPE_f

# laplacian of scalar field a
@njit(cache=True, parallel=True)
def laplacian_2d(a, dx, dy):
	output = np.zeros_like(a)
	nlat = nlong = i = j = 0;
	inv_dx = inv_dy = 0.0;

	nlat = a.shape[0]
	nlong = a.shape[1]
	inv_dy = 1/dy
	for i in prange(1, nlat-1):
		inv_dx = dx[i]
		for j in prange(nlon):
			output[i,j] = (low_level.scalar_gradient_x_2D(a,dx,nlon,i,j) - low_level.scalar_gradient_x_2D(a,dx,nlon,i,j))*inv_dx + (low_level.scalar_gradient_y_2D(a,dy,nlat,i+1,j) - low_level.scalar_gradient_y_2D(a,dy,nlat,i-1,j))*inv_dy
	return output

@njit(cache=True, parallel=True)
def laplacian_3d(a, dx, dy, dz):
	output = np.zeros_like(a)
	nlat = nlon = nlevels = i = j = k = 0;
	inv_dx = inv_dy = 0.0;

	nlat = a.shape[0]
	nlong = a.shape[1]
	nlevels = a.shape[2]
	inv_dy = 1/dy
	for i in prange(1, nlat-1):
		inv_dx = 1/dx[i]
		for j in prange(nlon):
			for k in prange(nlevels-1):
				output[i,j,k] = (low_level.scalar_gradient_x(a,dx,nlon,i,j,k) - low_level.scalar_gradient_x(a,dx,nlon,i,j,k))*inv_dx + (low_level.scalar_gradient_y(a,dy,nlat,i+1,j,k) - low_level.scalar_gradient_y(a,dy,nlat,i-1,j,k))*inv_dy + (low_level.scalar_gradient_z(a,dz,i,j,k+1)-low_level.scalar_gradient_z(a,dz,i,j,k-1))/(2*dz[k])
	return output				

# divergence of (a*u) where a is a scalar field and u is the atmospheric velocity field
@njit(cache=True, parallel=True)
def divergence_with_scalar(a, u, v, w, dx, dy, pressure_levels):
	output = np.zeros_like(a)
	au = a*u
	av = a*v
	aw = a*w

	nlat = output.shape[0]
	nlon = output.shape[1]
	nlevels = output.shape[2]

	for i in prange(nlat):
		for j in prange(nlon):
			for k in prange(nlevels):
				output[i,j,k] = low_level.scalar_gradient_x(au,dx,nlon,i,j,k) + low_level.scalar_gradient_y(av,dy,nlat,i,j,k) + low_level.scalar_gradient_z_1D(aw[i,j,:],pressure_levels,k)
				
	return output				

@njit(cache=True, parallel=True)
def radiation_calculation(temperature_world, potential_temperature, pressure_levels, heat_capacity_earth, albedo, insolation, lat, lon, t, dt, day, year, axial_tilt):
	# calculate change in temperature of ground and atmosphere due to radiative imbalance
	nlat = nlon = nlevels = i = j = k = 0
	fl = 0.1

	# np.ndarray upward_radiation,downward_radiation,optical_depth,Q,temperature_atmos

	sun_lat = inv_day = 0.0

	inv_day = 1/(24*60*60)


	temperature_atmos = low_level.theta_to_t(potential_temperature,pressure_levels)
	nlat = temperature_atmos.shape[0]	
	nlon = temperature_atmos.shape[1]
	nlevels = temperature_atmos.shape[2]

	upward_radiation = np.zeros(nlevels)
	downward_radiation = np.zeros(nlevels)
	Q = np.zeros(nlevels)

	for i in prange(nlat):
		
		sun_lat = low_level.surface_optical_depth(lat[i])
		optical_depth = sun_lat*(fl*(pressure_levels/pressure_levels[0]) + (1-fl)*(pressure_levels/pressure_levels[0])**4)
		
		for j in prange(nlon):
			
			# calculate upward longwave flux, bc is thermal radiation at surface
			upward_radiation[0] = low_level.thermal_radiation(temperature_world[i,j])
			for k in np.arange(1,nlevels):
				upward_radiation[k] = (upward_radiation[k-1] - (optical_depth[k]-optical_depth[k-1])*(low_level.thermal_radiation(temperature_atmos[i,j,k])))/(1 + optical_depth[k-1] - optical_depth[k])

			# calculate downward longwave flux, bc is zero at TOA (in model)
			downward_radiation[-1] = 0
			for k in np.arange(0,nlevels-1)[::-1]:
				downward_radiation[k] = (downward_radiation[k+1] - low_level.thermal_radiation(temperature_atmos[i,j,k])*(optical_depth[k+1]-optical_depth[k]))/(1 + optical_depth[k] - optical_depth[k+1])
			
			# gradient of difference provides heating at each level
			for k in np.arange(nlevels):
				Q[k] = -287*temperature_atmos[i,j,k]*low_level.scalar_gradient_z_1D(upward_radiation-downward_radiation,pressure_levels,k)/(1000*pressure_levels[k])
				# approximate SW heating of ozone
				if pressure_levels[k] < 400*100:
					Q[k] += low_level.solar(75,lat[i],lon[j],t,day, year, axial_tilt)*inv_day*(100/pressure_levels[k])

			temperature_atmos[i,j,:] += Q*dt

			# update surface temperature with shortwave radiation flux
			temperature_world[i,j] += dt*((1-albedo[i,j])*(low_level.solar(insolation,lat[i],lon[j],t, day, year, axial_tilt) + downward_radiation[0]) - upward_radiation[0])/heat_capacity_earth[i,j] 
	
	return temperature_world, low_level.t_to_theta(temperature_atmos,pressure_levels)

@njit(cache=True, parallel=True)
def velocity_calculation(u, v, w, pressure_levels, geopotential, potential_temperature, coriolis, gravity, dx, dy, dt):
	# introduce temporary arrays to update velocity in the atmosphere
	u_temp = np.zeros_like(u)
	v_temp = np.zeros_like(v)
	w_temp = np.zeros_like(u)

	nlat = nlon = nlevels = i = j = k = 0

	nlat = geopotential.shape[0]
	nlon = geopotential.shape[1]
	nlevels = len(pressure_levels)

	# calculate acceleration of atmosphere using primitive equations on beta-plane
	for i in prange(2,nlat-2):
		for j in prange(nlon):
			for k in prange(nlevels):
				
				u_temp[i,j,k] += dt*( -u[i,j,k]*low_level.scalar_gradient_x(u,dx,nlon,i,j,k) - v[i,j,k]*low_level.scalar_gradient_y(u,dy,nlat,i,j,k) - w[i,j,k]*low_level.scalar_gradient_z_1D(u[i,j,:],pressure_levels,k) + coriolis[i]*v[i,j,k] - low_level.scalar_gradient_x(geopotential,dx,nlon,i,j,k) - 1E-4*u[i,j,k])
				v_temp[i,j,k] += dt*( -u[i,j,k]*low_level.scalar_gradient_x(v,dx,nlon,i,j,k) - v[i,j,k]*low_level.scalar_gradient_y(v,dy,nlat,i,j,k) - w[i,j,k]*low_level.scalar_gradient_z_1D(v[i,j,:],pressure_levels,k) - coriolis[i]*u[i,j,k] - low_level.scalar_gradient_y(geopotential,dy,nlat,i,j,k) - 1E-4*v[i,j,k])
	u += u_temp
	v += v_temp
	
	# approximate surface friction
	u[:,:,0] *= 0.8
	v[:,:,0] *= 0.8

	return u,v

@njit(cache=True, parallel=True)
def w_calculation(u, v, w, pressure_levels, geopotential, potential_temperature, coriolis, gravity, dx, dy, dt):
	w_temp = np.zeros_like(u)
	temperature_atmos = low_level.theta_to_t(potential_temperature,pressure_levels) 

	nlat = nlon = nlevels = i = j = k = 0

	nlat = geopotential.shape[0]
	nlon = geopotential.shape[1]
	nlevels = len(pressure_levels)
	
	for i in prange(2,nlat-2):
		for j in prange(nlon):
			# Not prange for k, because w_{k} depends on w_{k-1}
			for k in range(1,nlevels):
				w_temp[i,j,k] = w_temp[i,j,k-1] - (pressure_levels[k]-pressure_levels[k-1])*pressure_levels[k]*gravity*( low_level.scalar_gradient_x(u,dx,nlon,i,j,k) + low_level.scalar_gradient_y(v,dy,nlat,i,j,k) )/(287*temperature_atmos[i,j,k])

	w += w_temp

	return w	


def smoothing_3D(a, smooth_parameter, vert_smooth_parameter=0.5):
	nlat = a.shape[0]
	nlon = a.shape[1]
	nlevels = a.shape[2]
	smooth_parameter *= 0.5

	test = np.fft.fftn(a)
	test[int(nlat*smooth_parameter):int(nlat*(1-smooth_parameter)),:,:] = 0
	test[:,int(nlon*smooth_parameter):int(nlon*(1-smooth_parameter)),:] = 0
	test[:,:,int(nlevels*vert_smooth_parameter):int(nlevels*(1-vert_smooth_parameter))] = 0
	return np.fft.ifftn(test).real	

